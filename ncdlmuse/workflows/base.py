# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""NCDLMUSE base processing workflows."""

import json
import logging
import os
import re
import shutil
import subprocess
import time
from importlib import resources as importlib_resources
from pathlib import Path

# ruff: noqa: F401
import pandas as pd
import torch
from bids.layout import Query
from nipype.interfaces import utility as niu
from nipype.pipeline import engine as pe
from niworkflows.engine.workflows import LiterateWorkflow as Workflow
from niworkflows.interfaces.bids import BIDSDataGrabber

# Add for plotting
from niworkflows.interfaces.reportlets.masks import ROIsPlot

from .. import config
from ..interfaces.bids import DerivativesDataSink
from ..interfaces.ncdlmuse import NiChartDLMUSE
from ..interfaces.reports import (
    ErrorReportlet,
    ExecutionProvenanceReportlet,
    SegmentationQCSummary,
    SubjectSummary,
    WorkflowProvenanceReportlet,
)
from ..utils.bids import get_entities_from_file

LOGGER = config.loggers.workflow

def init_ncdlmuse_wf(
    name='ncdlmuse_wf'
):
    """Initialize the top-level NCDLMUSE workflow.

    Handles BIDS discovery based on config settings, iterates over subjects/sessions,
    creates subject-specific runner workflows that run the core DLMUSE
    sub-workflow, and save outputs to the BIDS derivatives directory
    using DataSinks. Parameters are retrieved from the global config object.

    Workflow Graph
    --------------
    This workflow queries the BIDS layout for T1w files based on participant/session
    labels specified in the configuration. It then aggregates multiple instances of
    :py:func:`~ncdlmuse.workflows.base.init_single_subject_wf`.

    .. workflow::
        :graph2use: orig
        :simple_form: yes

        from ncdlmuse.workflows.base import init_ncdlmuse_wf
        wf = init_ncdlmuse_wf(name='ncdlmuse_wf')

    Parameters
    ----------
    name : str
        Name of the workflow (default 'ncdlmuse_wf').

    Returns
    -------
    workflow : nipype.pipeline.engine.Workflow
        The assembled Nipype workflow object.
    """
    # --- Retrieve parameters from config --- #
    output_dir = config.execution.output_dir
    work_dir = config.execution.work_dir
    layout = config.execution.layout # Get BIDS layout
    subject_list = config.execution.participant_label # List of subjects to process
    session_list = config.execution.session_label # Optional list of sessions
    device = config.workflow.dlmuse_device
    nthreads = config.nipype.n_procs
    model_folder = config.workflow.dlmuse_model_folder
    derived_roi_mappings_file = config.workflow.dlmuse_derived_roi_mappings_file
    muse_roi_mappings_file = config.workflow.dlmuse_muse_roi_mappings_file
    all_in_gpu = config.workflow.dlmuse_all_in_gpu
    disable_tta = config.workflow.dlmuse_disable_tta
    clear_cache = config.workflow.dlmuse_clear_cache

    # --- Basic Workflow Setup --- #
    workflow = Workflow(name=name)
    workflow.base_dir = str(work_dir)
    workflow.__desc__ = """\
Top-level workflow for NCDLMUSE processing.

This workflow queries the BIDS dataset for T1w files based on configuration,
iterates over subjects/sessions, instantiates a subject-specific workflow
(:py:func:`~ncdlmuse.workflows.base.init_single_subject_wf`) for each T1w file,
and manages overall execution settings.
"""
    # Initialize default node configs
    for node in workflow._get_all_nodes():
        if not hasattr(node, 'config') or node.config is None:
            node.config = {
                'memory_gb': None,
                'memory_mb': None,
                'num_threads': None,
                'num_cpus': None,
                'rules': True,
            }

    LOGGER.info(f'Initializing {name}')
    LOGGER.info(f'Work directory: {work_dir}')
    LOGGER.info(f'Output directory: {output_dir}')

    # Use config.execution.ncdlmuse_dir as the base for derivatives
    ncdlmuse_output_dir = Path(config.execution.ncdlmuse_dir)

    # Check if layout is available
    if not layout:
        raise RuntimeError(
            'BIDS Layout is not available in the configuration. Cannot query for T1w files.'
            ' Check BIDS dataset indexing in parser stage.'
        )

    # --- Pre-load package data once ---
    try:
        with importlib_resources.as_file(
            importlib_resources.files('ncdlmuse.data') / 'MUSE_mapping_consecutive_indices.tsv'
        ) as p:
            mapping_tsv = str(p)
        with importlib_resources.as_file(
            importlib_resources.files('ncdlmuse.data') / 'io_spec.json'
        ) as p:
            io_spec = str(p)
        # Preload the ROI list TSV
        with importlib_resources.as_file(
            importlib_resources.files('ncdlmuse.data') / 'MUSE_ROI_complete_list.tsv'
        ) as p:
            roi_list_tsv = str(p)
    except FileNotFoundError as e:
        LOGGER.error(f'Required package data file not found: {e}')
        raise

    # --- Copy atlas mapping once if it doesn't exist --- #
    target_tsv_path = Path(output_dir) / 'seg-DLMUSE_dseg.tsv' # Uses output_dir, OK
    target_json_path = Path(output_dir) / 'seg-DLMUSE_dseg.json' # Uses output_dir, OK

    # Call the copy function if either file is missing
    if not target_tsv_path.exists() or not target_json_path.exists():
        LOGGER.info(
            f'Target atlas mapping TSV ({target_tsv_path}) or '
            f'JSON ({target_json_path}) does not exist. Attempting to copy.'
        )
        _copy_atlas_mapping(mapping_tsv, output_dir)
    else:
        LOGGER.info(
            f'Target atlas mappings TSV and JSON already exist in {output_dir}. Skipping copy.'
            )

    # --- Iterate over subjects and sessions, query T1w files --- #
    processed_file_count = 0
    for subject_id in subject_list:
        query_params = {
            'subject': subject_id,
            'suffix': 'T1w',
            'extension': ['.nii', '.nii.gz'],
            'return_type': 'file'
        }

        sessions_to_query = session_list if session_list else [None]

        for session_id in sessions_to_query:
            if session_id:
                query_params['session'] = session_id
            else:
                query_params.pop('session', None)

            subj_sess_prefix = f'sub-{subject_id}'
            if session_id:
                subj_sess_prefix += f'_ses-{session_id}'

            LOGGER.info(f'Querying T1w files for {subj_sess_prefix} with params: {query_params}')
            try:
                t1w_files = layout.get(**query_params)
            except Exception as e:
                import traceback
                LOGGER.error(f'Error querying BIDS layout for {subj_sess_prefix}: {e}')
                print('--- Full Traceback --- ')
                traceback.print_exc()
                print('--- End Traceback --- ')
                continue

            if not t1w_files:
                LOGGER.warning(f'No T1w files found for {subj_sess_prefix}. Skipping.')
                continue

            LOGGER.info(
                f'Found {len(t1w_files)} T1w file(s) for {subj_sess_prefix}. '
                f'Creating workflow(s)...'
            )

            # Iterate through the found T1w files for this subject/session
            for t1w_file in t1w_files:
                processed_file_count += 1
                LOGGER.info(f'Processing T1w file: {t1w_file}')
                entities = get_entities_from_file(t1w_file, layout=layout)

                # Use subject/session from entities for consistency in naming
                subj_ent_id = entities.get('subject', subject_id)
                sess_ent_id = entities.get('session', session_id)
                node_prefix = f'sub-{subj_ent_id}' + (f'_ses-{sess_ent_id}' if sess_ent_id else '')

                # Determine T1w JSON sidecar path
                p = Path(t1w_file)
                if p.name.endswith('.nii.gz'):
                    base = p.with_name(p.name[:-7])
                elif p.suffix == '.nii':
                    base = p.with_suffix('')
                else:
                    base = p.with_suffix('') # Fallback for unexpected extensions
                sidecar = base.with_suffix('.json')
                t1w_json = str(sidecar) if sidecar.exists() else None
                if not t1w_json:
                    LOGGER.warning(f'No T1w JSON sidecar found at {sidecar}')

                # Define reportlets directory for this subject/session
                # This will be passed to init_single_subject_wf
                # The base reportlets_dir is <work_dir>/reportlets
                reportlets_subj_anat_dir = (
                    Path(work_dir) / 'reportlets' /
                    f"sub-{entities.get('subject', 'UNKNOWN')}" /
                    ('anat' if entities.get('session') is None
                     else f"ses-{entities.get('session')}/anat")
                )

                LOGGER.info(f'Creating workflow for {node_prefix} ({Path(t1w_file).name})')
                subject_wf = init_single_subject_wf(
                    subject_id=subj_ent_id,
                    _t1w_file_path=t1w_file,
                    _t1w_json_path=t1w_json,
                    _current_t1w_entities=entities,
                    mapping_tsv=mapping_tsv,
                    io_spec=io_spec,
                    roi_list_tsv=roi_list_tsv,
                    derivatives_dir=ncdlmuse_output_dir,
                    reportlets_dir=reportlets_subj_anat_dir,
                    device=device,
                    nthreads=nthreads,
                    work_dir=work_dir,
                    model_folder=model_folder,
                    derived_roi_mappings_file=derived_roi_mappings_file,
                    muse_roi_mappings_file=muse_roi_mappings_file,
                    all_in_gpu=all_in_gpu,
                    disable_tta=disable_tta,
                    clear_cache=clear_cache,
                    name=f'single_subject_{node_prefix}_wf',
                )
                workflow.add_nodes([subject_wf])
    # --- END MODIFIED SECTION ---

    # Final check if any workflows were actually added
    if processed_file_count == 0:
        raise RuntimeError(
            'No T1w files were found for the specified subjects/sessions. '
            'Check BIDS dataset structure and participant/session labels.'
        )
    else:
        LOGGER.info(f'Added {processed_file_count} single-T1w processing workflows.')

    return workflow


def init_single_subject_wf(
    subject_id: str,
    _t1w_file_path: str,
    _t1w_json_path: str | None,
    _current_t1w_entities: dict,
    mapping_tsv,
    io_spec,
    roi_list_tsv,
    derivatives_dir,
    reportlets_dir,
    device='cpu',
    nthreads=1,
    work_dir=None,
    model_folder=None,
    derived_roi_mappings_file=None,
    muse_roi_mappings_file=None,
    all_in_gpu=False,
    disable_tta=False,
    clear_cache=False,
    name='single_subject_wf',
):
    """Initialize the NCDLMUSE processing pipeline for a single subject/session T1w.

    This workflow takes the original BIDS T1w path, copies the NIfTI file to the
    working directory, runs the core DLMUSE sub-workflow on the copy,
    and saves the results using DataSinks, naming outputs according to BIDS conventions.
    It also generates HTML summary reports.

    Workflow Graph
        .. workflow::
            :graph2use: orig
            :simple_form: yes

            from pathlib import Path
            from ncdlmuse.workflows.base import init_single_subject_wf
            from ncdlmuse import config # Minimal config for example

            # Example config setup (minimal)
            config.environment.version = '0.0.0-dev'
            config.environment.nipype_version = '1.8.0'
            config.execution.cmdline = ['ncdlmuse', 'bids', 'out', 'participant']
            config.execution.ncdlmuse_dir = Path('/tmp/ncdlmuse_example_derivs')


            # Define placeholder paths and entities
            t1w_file_path = '/test/bids/sub-01/anat/sub-01_T1w.nii.gz'
            t1w_json_path = '/test/bids/sub-01/anat/sub-01_T1w.json' # Can be None
            mapping_tsv_path = '/test/data/mapping.tsv' # Placeholder
            io_spec_path = '/test/data/io_spec.json' # Placeholder
            roi_list_tsv_path = '/test/data/roi_list.tsv' # Placeholder
            example_derivatives_dir = '/test/derivatives/ncdlmuse'
            example_reportlets_dir = '/test/work/reportlets/ncdlmuse/sub-01/anat'
            example_entities = {'subject': '01', 'datatype': 'anat', 'suffix': 'T1w'}
            example_work_dir = '/test/work'


            # Create the workflow instance
            wf = init_single_subject_wf(
                subject_id='01',
                _t1w_file_path=t1w_file_path,
                _t1w_json_path=t1w_json_path,
                _current_t1w_entities=example_entities,
                mapping_tsv=mapping_tsv_path,
                io_spec=io_spec_path,
                roi_list_tsv=roi_list_tsv_path,
                derivatives_dir=example_derivatives_dir,
                reportlets_dir=example_reportlets_dir,
                device='cpu',
                nthreads=1,
                work_dir=example_work_dir,
                name='single_subject_wf_example'
            )

    Parameters
    ----------
    subject_id : str
        Subject identifier (e.g., '01').
    _t1w_file_path : str
        Absolute path to the specific T1w NIfTI file for this workflow instance.
    _t1w_json_path : str or None
        Absolute path to the T1w JSON sidecar for the specific T1w file, if it exists.
    _current_t1w_entities : dict
        BIDS entities dictionary corresponding to the specific `_t1w_file_path`.
    mapping_tsv : str
        Path to the MUSE mapping TSV file `data/MUSE_mapping_consecutive_indices.tsv`.
    io_spec : str
        Path to the BIDS-Derivatives `io_spec.json` file.
    roi_list_tsv : str
        Path to the MUSE ROI complete list TSV file `data/MUSE_ROI_complete_list.tsv`.
    derivatives_dir : str
        Path to the NCDLMUSE derivatives directory (e.g., `<output>/ncdlmuse`).
    reportlets_dir : str
        Path to the directory for SVG reportlets for this subject
        (e.g., `<work_dir>/reportlets/ncdlmuse/sub-<label>/anat`).
    device : {'cpu', 'cuda', 'mps'}
        Computation device passed to the DLMUSE core workflow.
    nthreads : int
        Maximum number of threads (relevant for Nipype execution engine).
    work_dir : str
        Path to the main working directory for this workflow.
    model_folder : str or None, optional
        Path to the custom model folder for NiChart_DLMUSE.
    derived_roi_mappings_file : str or None, optional
        Path to the derived ROI mappings file for NiChart_DLMUSE.
    muse_roi_mappings_file : str or None, optional
        Path to the MUSE ROI mappings file for NiChart_DLMUSE.
    all_in_gpu : bool, optional
        Run all operations on GPU if available.
    disable_tta : bool, optional
        Disable Test-Time Augmentation.
    clear_cache : bool, optional
        Clear model cache before running.
    name : str
        Workflow name (default: 'single_subject_wf').

    Returns
    -------
    workflow : :py:class:`niworkflows.engine.workflows.LiterateWorkflow`
        The assembled Nipype workflow object for a single subject.
    """
    workflow = Workflow(name=name) # Use LiterateWorkflow
    if work_dir:
        workflow.base_dir = str(work_dir)

    subject_id_str = _prefix(subject_id)

    # Workflow description (for reports)
    workflow.__desc__ = f"""\
NCDLMUSE: NiChart DLMUSE BIDS App version {config.environment.version}

This workflow performs deep-learning based brain segmentation using NiChart_DLMUSE
on T1-weighted (T1w) images for subject {subject_id_str}.
The core of the processing involves the `NiChart_DLMUSE` tool to generate
volumetric segmentations and related measurements.

The results include:
* Volumetric segmentation images in T1w space.
* Brain masks.
* JSON files containing quantitative volume information.
* HTML summary reports detailing processing steps and quality control metrics.
"""
    workflow.__postdesc__ = f"""\
For more details on the NCDLMUSE pipeline and methodologies, please consult the
official documentation.

NCDLMUSE is built using Nipype {config.environment.nipype_version}
(@nipype1; @nipype2; RRID:SCR_002502).
"""

    # Initialize default node configs
    for node in workflow._get_all_nodes():
        if not hasattr(node, 'config') or node.config is None:
            node.config = {
                'memory_gb': None,
                'memory_mb': None,
                'num_threads': None,
                'num_cpus': None,
                'rules': True,
            }

    # --- BIDSDataGrabber to source T1w and its JSON sidecar ---
    subject_data_for_grabber = {
        't1w': [_t1w_file_path],
        'bold': [],
        't2w': [],
        'flair': [],
        'roi': [],
        'fmap': [],
        'sbref': [],
        'dwi': [],
        'pet': [],
        'asl': [],
    }

    bidssrc = pe.Node(
        BIDSDataGrabber(
            subject_data=subject_data_for_grabber,
            subject_id=subject_id,
            anat_only=True
        ),
        name='bidssrc'
    )

    # --- Define InputNode (for static/global files not handled by BIDSDataGrabber) ---
    inputnode = pe.Node(
        niu.IdentityInterface(
            fields=[
                'mapping_tsv',
                'io_spec',
                'roi_list_tsv',
            ]
        ),
        name='inputnode',
    )
    inputnode.inputs.mapping_tsv = mapping_tsv
    inputnode.inputs.io_spec = io_spec
    inputnode.inputs.roi_list_tsv = roi_list_tsv

    # --- Define OutputNode --- #
    outputnode = pe.Node(
        niu.IdentityInterface(
            fields=[
                'dlmuse_segmentation',  # NIfTI segmented T1w
                'dlicv_mask',  # NIfTI brain mask
                'brain_mask_meta',  # Dict for mask JSON
                'volumes_json_path', # Path to the generated JSON
                # Keep fields if needed for QC or other purposes
            ]
        ),
        name='outputnode',
    )

    # --- Instantiate Internal Nodes ---

    # NiChartDLMUSE node (configured directly from function args)
    dlmuse_node = pe.Node(
        NiChartDLMUSE(
            device=device,
            all_in_gpu=all_in_gpu,
            disable_tta=disable_tta,
            clear_cache=clear_cache,
            **(({'model_folder': model_folder}) if model_folder else {}),
            **(
                ({'derived_roi_mappings_file': derived_roi_mappings_file})
                if derived_roi_mappings_file
                else {}
            ),
            **(
                ({'muse_roi_mappings_file': muse_roi_mappings_file})
                if muse_roi_mappings_file
                else {}
            ),
        ),
        name='nichartdlmuse_node',
    )

    # Node to create volumes JSON (pre-datasink)
    create_volumes_json_node = pe.Node(
        niu.Function(
            input_names=[
                'volumes_csv',
                'source_t1w_json_path',
                'device_used',
                'roi_list_tsv',
            ],
            output_names=['output_json_path'],
            function=_create_volumes_json_file,
        ),
        name='create_volumes_json_node',
    )
    create_volumes_json_node.inputs.device_used = device
    create_volumes_json_node.inputs.source_t1w_json_path = (
        _t1w_json_path if _t1w_json_path and Path(_t1w_json_path).exists() else None
    )

    # Node to create metadata for brain mask JSON sidecar
    create_meta_node = pe.Node(
        niu.Function(
            input_names=['raw_source_file'],
            output_names=['meta_dict'],
            function=_create_brain_mask_meta,
        ),
        name=f'{subject_id_str}_create_meta',
        run_without_submitting=True,
    )

    # Node to create metadata for segmentation JSON sidecar
    create_seg_meta_node = pe.Node(
        niu.Function(
            input_names=['raw_source_file'],
            output_names=['meta_dict'],
            function=_create_segmentation_meta,
        ),
        name=f'{subject_id_str}_create_seg_meta',
        run_without_submitting=True,
    )

    # --- Connect Workflow --- #
    # Connect BIDSDataGrabber outputs and InputNode to processing nodes
    workflow.connect([
        (bidssrc, dlmuse_node, [(('t1w', _select_first_from_list), 'input_image')]),
        (bidssrc, create_meta_node, [(('t1w', _select_first_from_list), 'raw_source_file')]),
        (bidssrc, create_seg_meta_node, [(('t1w', _select_first_from_list), 'raw_source_file')]),
        (inputnode, create_volumes_json_node, [('roi_list_tsv', 'roi_list_tsv')]),
    ])

    # Connect internal processing nodes
    workflow.connect(dlmuse_node, 'dlmuse_volumes', create_volumes_json_node, 'volumes_csv')

    # Connect processing nodes to OutputNode
    workflow.connect(dlmuse_node, 'dlmuse_segmentation', outputnode, 'dlmuse_segmentation')
    workflow.connect(dlmuse_node, 'dlicv_mask', outputnode, 'dlicv_mask')
    workflow.connect(create_meta_node, 'meta_dict', outputnode, 'brain_mask_meta')
    workflow.connect(
        create_volumes_json_node, 'output_json_path',
        outputnode, 'volumes_json_path'
        )

    # --- Add DataSink Nodes --- #

    # Sink for DLMUSE segmentation NIfTI
    ds_seg_nii = pe.Node(
        DerivativesDataSink(
            base_directory=str(derivatives_dir),
            compress=True,
            datatype='anat',
            space='T1w',
            segmentation='DLMUSE',
            suffix='dseg',
            extension='nii.gz',
        ),
        name='ds_seg_nii',
        run_without_submitting=True,
    )

    # Sink for Brain Mask NIfTI
    ds_brain_mask = pe.Node(
        DerivativesDataSink(
            base_directory=str(derivatives_dir),
            compress=True,
            datatype='anat',
            desc='brain',
            suffix='mask',
            extension='nii.gz',
        ),
        name='ds_brain_mask',
        run_without_submitting=True,
    )

    # Pathfinder for Volumes JSON path
    ds_volumes_json_pathfinder = pe.Node(
        DerivativesDataSink(
            base_directory=str(derivatives_dir),
            compress=False,
            datatype='anat',
            suffix='T1w',
            extension='json',
            check_hdr=False,
        ),
        name='ds_volumes_json_pathfinder',
        run_without_submitting=True,
    )

    # Node to copy the generated JSON to the final path
    copy_json_node = pe.Node(
        niu.Function(
            input_names=['in_file', 'out_file'],
            output_names=['copied_file'],
            function=_copy_single_file
        ),
        name='copy_volumes_json'
    )

    # --- Connect Processing Nodes to DataSinks --- #

    # Connect Inputs required by all datasinks
    # Source file for datasinks comes from BIDSDataGrabber
    workflow.connect([
        (bidssrc, ds_seg_nii, [(('t1w', _select_first_from_list), 'source_file')]),
        (bidssrc, ds_brain_mask, [(('t1w', _select_first_from_list), 'source_file')]),
        (bidssrc, ds_volumes_json_pathfinder, [(('t1w', _select_first_from_list), 'source_file')]),
        (inputnode, ds_volumes_json_pathfinder, [('io_spec', 'io_spec')])
    ])

    # a) Connect DLMUSE segmentation NIfTI
    workflow.connect(dlmuse_node, 'dlmuse_segmentation', ds_seg_nii, 'in_file')
    workflow.connect(create_seg_meta_node, 'meta_dict', ds_seg_nii, 'meta_dict')

    # b) Connect Brain mask NIfTI + metadata
    workflow.connect(dlmuse_node, 'dlicv_mask', ds_brain_mask, 'in_file')
    workflow.connect(create_meta_node, 'meta_dict', ds_brain_mask, 'meta_dict')

    # d) Connect Volumes JSON generation and copying
    workflow.connect(
        create_volumes_json_node, 'output_json_path',
        ds_volumes_json_pathfinder, 'in_file'
        )
    # Connect temp JSON path to copy node in_file
    workflow.connect(create_volumes_json_node, 'output_json_path', copy_json_node, 'in_file')
    # Connect pathfinder output path to copy node out_file
    workflow.connect(ds_volumes_json_pathfinder, 'out_file', copy_json_node, 'out_file')

    # --- Add Reportlet Generation Nodes --- #
    LOGGER.info(
        f'[{subject_id_str}] Adding reportlet nodes. '
        f'Reportlets will be saved to: {reportlets_dir}'
    )
    # Ensure reportlets_dir (for SVGs) is a Path and exists
    current_reportlets_dir = Path(reportlets_dir)
    current_reportlets_dir.mkdir(parents=True, exist_ok=True)

    # --- Instead of using reportlets_dir/sub/ses/anat, use derivatives_dir/sub/figures ---
    figures_dir = Path(derivatives_dir) / f'sub-{_current_t1w_entities["subject"]}' / 'figures'
    figures_dir.mkdir(parents=True, exist_ok=True)

    # Get base filename from original T1w file path
    t1w_path = Path(_t1w_file_path)
    base_filename = t1w_path.stem.replace('.nii', '').replace('.gz', '')

    # Reportlet for Brain Mask
    plot_brain_mask = pe.Node(
        ROIsPlot(
            colors=['#FF0000'],  # Red contour
            levels=[0.5],
            out_report=str(figures_dir / f'{base_filename}_desc-brainMask_T1w.svg')
        ),
        name='plot_brain_mask',
        mem_gb=0.2 # Slightly more memory for plotting
    )

    # Reportlet for DLMUSE Segmentation
    plot_dlmuse_seg = pe.Node(
        ROIsPlot(
            out_report=str(figures_dir / f'{base_filename}_desc-dlmuseSegmentation_T1w.svg')
        ),
        name='plot_dlmuse_seg',
        mem_gb=0.2 # Slightly more memory for plotting
    )

    # Connect T1w (background) and masks/segmentations (foreground)
    # T1w input for plots comes from BIDSDataGrabber
    workflow.connect([
        (bidssrc, plot_brain_mask, [(('t1w', _select_first_from_list), 'in_file')]),
        (dlmuse_node, plot_brain_mask, [('dlicv_mask', 'in_rois')])
    ])

    workflow.connect([
        (bidssrc, plot_dlmuse_seg, [(('t1w', _select_first_from_list), 'in_file')]),
        (dlmuse_node, plot_dlmuse_seg, [('dlmuse_segmentation', 'in_rois')])
    ])

    # --- END Reportlet Generation ---

    # === HTML Report Generation ===

    # 1. Subject Summary Report (Primary HTML Summary)
    subject_summary_node = pe.Node(
        SubjectSummary(
            subject_id=_current_t1w_entities.get('subject', 'UNKNOWN')
        ), # Use subject from the specific T1w entities
        name='subject_summary_node',
        run_without_submitting=True
    )
    ds_report_summary = pe.Node(
        DerivativesDataSink(
            base_directory=str(derivatives_dir),
            desc='summary',
            datatype='figures',
            suffix='T1w',
            extension='.html',
            source_file=_t1w_file_path,  # Use original T1w file to maintain entities
        ),
        name='ds_report_summary',
        run_without_submitting=True,
    )
    workflow.connect([
        (bidssrc, subject_summary_node, [(('t1w', _make_list), 't1w')]),
        (subject_summary_node, ds_report_summary, [('out_report', 'in_file')]),
    ])

    # 2. Execution Provenance Report (About this NCDLMUSE run)
    exec_provenance_node = pe.Node(
        ExecutionProvenanceReportlet(
            pipeline_name='ncdlmuse',
            version=config.environment.version,
            command=' '.join(config.execution.cmdline),
            timestamp=time.strftime('%Y-%m-%d %H:%M:%S %Z')
        ),
        name='execution_provenance_node',
        run_without_submitting=True
    )
    ds_report_about = pe.Node(
        DerivativesDataSink(
            base_directory=str(derivatives_dir),
            desc='about',
            datatype='figures',
            suffix='T1w',
            extension='.html',
            source_file=_t1w_file_path,  # Use original T1w file to maintain entities
        ),
        name='ds_report_about',
        run_without_submitting=True,
    )
    workflow.connect([
        (exec_provenance_node, ds_report_about, [('out_report', 'in_file')]),
    ])

    # 3. Error Reportlet (Checks DLMUSE outputs)
    check_dlmuse_outputs_node = pe.Node(
        niu.Function(
            input_names=['segmentation_file', 'volumes_csv_file'],
            output_names=['error_messages_list'],
            function=_check_dlmuse_outputs # Defined below
        ),
        name='check_dlmuse_outputs_node',
        run_without_submitting=True
    )
    workflow.connect(dlmuse_node, 'dlmuse_segmentation',
                     check_dlmuse_outputs_node, 'segmentation_file')
    workflow.connect(dlmuse_node, 'dlmuse_volumes',
                     check_dlmuse_outputs_node, 'volumes_csv_file')

    error_report_node = pe.Node(
        ErrorReportlet(),
        name='error_report_node'
    )
    workflow.connect(check_dlmuse_outputs_node, 'error_messages_list',
                     error_report_node, 'error_messages')

    ds_error_report = pe.Node(
        DerivativesDataSink(
            base_directory=str(derivatives_dir),
            desc='processingErrors',
            datatype='figures',
            suffix='T1w',
            extension='.html',
            source_file=_t1w_file_path,  # Use original T1w file to maintain entities
        ),
        name='ds_error_report',
        run_without_submitting=True,
    )
    workflow.connect([
        (error_report_node, ds_error_report, [('out_report', 'in_file')]),
    ])

    # 4. Workflow Provenance Reportlet (Software versions from JSON)
    workflow_provenance_report_node = pe.Node(
        WorkflowProvenanceReportlet(),
        name='workflow_provenance_report_node'
    )
    workflow.connect(create_volumes_json_node, 'output_json_path',
                     workflow_provenance_report_node, 'provenance_json_file')

    ds_workflow_provenance_report = pe.Node(
        DerivativesDataSink(
            base_directory=str(derivatives_dir),
            desc='workflowProvenance',
            datatype='figures',
            suffix='T1w',
            extension='.html',
            source_file=_t1w_file_path,  # Use original T1w file to maintain entities
        ),
        name='ds_workflow_provenance_report',
        run_without_submitting=True,
    )
    workflow.connect([
        (workflow_provenance_report_node, ds_workflow_provenance_report,
         [('out_report', 'in_file')]),
    ])

    # 5. Segmentation QC Reportlet (Volumes from JSON)
    segmentation_qc_report_node = pe.Node(
        SegmentationQCSummary(),
        name='segmentation_qc_report_node'
    )
    workflow.connect(create_volumes_json_node, 'output_json_path',
                     segmentation_qc_report_node, 'segmentation_qc_json_file')

    ds_segmentation_qc_report = pe.Node(
        DerivativesDataSink(
            base_directory=str(derivatives_dir),
            desc='segmentationVolumes',
            datatype='figures',
            suffix='T1w',
            extension='.html',
            source_file=_t1w_file_path,  # Use original T1w file to maintain entities
        ),
        name='ds_segmentation_qc_report',
        run_without_submitting=True,
    )
    workflow.connect([
        (segmentation_qc_report_node, ds_segmentation_qc_report, [('out_report', 'in_file')]),
    ])

    # Also generate the main subject-level HTML report
    ds_main_report = pe.Node(
        DerivativesDataSink(
            base_directory=str(derivatives_dir),
            desc='summary',
            datatype='figures',
            suffix='T1w',
            extension='.html',
            source_file=_t1w_file_path,  # Use original T1w file to maintain entities
            dismiss_entities=['session', 'acquisition'],  # Ensure this goes to subject level
        ),
        name='ds_main_report',
        run_without_submitting=True,
    )
    workflow.connect([
        (subject_summary_node, ds_main_report, [('out_report', 'in_file')]),
    ])
    # === END HTML Report Generation ===

    return clean_datasinks(workflow) # Apply clean_datasinks

# --- Helper functions for _create_volumes_json_file ---

def _check_dlmuse_outputs(segmentation_file, volumes_csv_file):
    """Check if DLMUSE outputs exist and are not empty."""
    from pathlib import Path

    error_messages = []
    if not (segmentation_file and Path(segmentation_file).exists() \
        and Path(segmentation_file).stat().st_size > 0):
        error_messages.append('NiChart_DLMUSE segmentation output NIfTI file is missing or empty.')
    if not (volumes_csv_file and Path(volumes_csv_file).exists() \
        and Path(volumes_csv_file).stat().st_size > 0):
        error_messages.append('NiChart_DLMUSE volumes output CSV file is missing or empty.')

    # If no errors were added, ensure the list is not empty & ErrorReportlet shows "no errors"
    # if not error_messages: # This logic is handled by ErrorReportlet if it receives an empty list
    #     pass # ErrorReportlet handles empty list by showing "no specific errors"
    return error_messages

def _copy_atlas_mapping(source_tsv_path_str, output_dir_str):
    """Copy the atlas mapping TSV and JSON files to the output directory if they don't exist,
    renaming them to seg-DLMUSE_dseg.*."""
    import os
    import shutil
    from pathlib import Path

    source_tsv_path = Path(source_tsv_path_str)
    output_dir = Path(output_dir_str)
    # Define the new target filenames
    target_tsv_filename = 'seg-DLMUSE_dseg.tsv'
    target_json_filename = 'seg-DLMUSE_dseg.json'

    target_tsv_path = output_dir / target_tsv_filename

    # Derive JSON paths from TSV path, using the source name to find the corresponding source json
    source_json_path = source_tsv_path.with_suffix('.json')
    target_json_path = output_dir / target_json_filename

    LOGGER.info(
    f'Checking for {target_tsv_filename} and {target_json_filename} in target '
    f'directory: {output_dir}')

    try:
        # Create directories if they don't exist
        output_dir.mkdir(parents=True, exist_ok=True)

        # Copy TSV if it doesn't exist
        if not target_tsv_path.exists():
            LOGGER.info(f'Copying atlas mapping TSV from {source_tsv_path} to {target_tsv_path}')
            shutil.copy2(source_tsv_path, target_tsv_path)
            LOGGER.info(f'Successfully copied atlas mapping TSV to {target_tsv_path}')
        else:
            LOGGER.info(
                f'Atlas mapping TSV {target_tsv_filename} already exists at '
                f'{target_tsv_path}. Skipping copy.'
                )

        # Copy JSON if source exists and target doesn't exist
        if source_json_path.exists():
            if not target_json_path.exists():
                LOGGER.info(
                    f'Copying atlas mapping JSON from {source_json_path} to {target_json_path}'
                    )
                shutil.copy2(source_json_path, target_json_path)
                LOGGER.info(f'Successfully copied atlas mapping JSON to {target_json_path}')
            else:
                LOGGER.info(
                    f'Atlas mapping JSON {target_json_filename} already exists at '
                    f'{target_json_path}. Skipping copy.'
                    )
        else:
             LOGGER.warning(
                f'Source atlas mapping JSON not found at {source_json_path}. Cannot copy.'
                )

        # No specific file path to return as multiple files are handled
        return None

    except Exception as e:
        LOGGER.error(f'Could not copy atlas mapping files to {output_dir}: {e}')
        return None


# --- Helper function for copying --- #
def _copy_single_file(in_file, out_file):
    """Copies a single file using shutil.copy2, creating destination directory."""
    import shutil
    from pathlib import Path
    out_path = Path(out_file)
    # Ensure parent directory exists
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(in_file, out_file)
    # The Function node needs to return the path to the created file
    return out_file


# --- Helper Function for Metadata --- #
def _create_brain_mask_meta(raw_source_file):
    """Generate metadata dictionary for brain mask JSON sidecar."""
    from nipype.utils.filemanip import ensure_list

    # Strip potential BIDS prefix if present, although absolute paths are more common
    raw_source_file = raw_source_file.replace('bids::', '')
    return {'Type': 'Brain', 'RawSources': ensure_list(raw_source_file)}


def _create_segmentation_meta(raw_source_file):
    """Generate metadata dictionary for segmentation JSON sidecar."""
    from nipype.utils.filemanip import ensure_list

    # Strip potential BIDS prefix if present
    raw_source_file = raw_source_file.replace('bids::', '')
    return {
        'Type': 'Brain',
        'RawSources': ensure_list(raw_source_file),
        'SkullStripped': True,
    }


# --- Helper Function for Snake Case --- #
def _to_snake_case(text):
    """Convert text to lowercase snake_case, handling CamelCase and separators."""
    if not isinstance(text, str):
        text = str(text)
    # Add underscores for CamelCase and Acronyms
    s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', text)
    s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1)
    # Replace any sequence of one or more non-alphanumeric chars with a single underscore
    s3 = re.sub(r'[^a-zA-Z0-9]+', '_', s2)
    # Convert to lowercase and remove leading/trailing underscores
    return s3.lower().strip('_')


# --- Helper Function to create volumes JSON with specific BIDS name --- #
def _create_volumes_json_file(
    volumes_csv,
    source_t1w_json_path,
    device_used,
    roi_list_tsv,
    source_file=None):
    """Create JSON with raw T1w metadata, provenance, and volumes, writing it to a file.

    Uses roi_list_tsv to map NiChartDLMUSE output keys to Full_Name.
    """

    # Imports required within the Nipype Function execution scope
    import json
    import os
    import re
    import subprocess  # noqa: F401
    from collections import OrderedDict
    from pathlib import Path

    import pandas as pd  # noqa: F401
    import torch  # noqa: F401

    from ncdlmuse import __version__ as bids_ncdlmuse_version
    from ncdlmuse import config

    # Define helper function locally for Nipype Function scope
    def _to_snake_case(text):
        """Convert text to lowercase snake_case, handling CamelCase and separators."""
        if not isinstance(text, str):
            text = str(text)
        # Add underscores for CamelCase and Acronyms
        s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', text)
        s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1)
        # Replace any sequence of one or more non-alphanumeric chars with a single underscore
        s3 = re.sub(r'[^a-zA-Z0-9]+', '_', s2)
        # Convert to lowercase and remove leading/trailing underscores
        return s3.lower().strip('_')

    LOGGER = config.loggers.workflow

    # --- Read ROI Name Mapping --- #
    roi_mapping = {}
    try:
        roi_df = pd.read_csv(roi_list_tsv, sep='\t')
        # Create mapping from snake_case(ID) -> snake_case(Full_Name)
        for _, row in roi_df.iterrows():
            # Ensure ID is treated as string before snake_casing
            input_key = _to_snake_case(str(row['ID']))
            output_key = _to_snake_case(row['Full_Name'])
            roi_mapping[input_key] = output_key
        LOGGER.info(f'Successfully created ROI mapping from {roi_list_tsv}. '
                    f'{len(roi_mapping)} entries.')
    except FileNotFoundError:
        LOGGER.error(f'ROI list file not found: {roi_list_tsv}. Cannot map volume names.')
        # Proceeding with original names for now, but logging error.
    except KeyError as e:
        LOGGER.error(f'Missing expected column ({e}) in ROI list file: {roi_list_tsv}. '
                    f'Cannot map volume names.')
    except Exception as e:
        LOGGER.error(f'Error reading or processing ROI list file {roi_list_tsv}: {e!r}')


    # 1. bids_meta: Read from the provided source T1w JSON sidecar path
    bids_meta_dict = {}
    if source_t1w_json_path:
        try:
            with open(source_t1w_json_path) as f:
                bids_meta_dict = json.load(f)
            LOGGER.info(f'Successfully loaded bids_meta from {source_t1w_json_path}')
        except FileNotFoundError:
            # This is expected if the sidecar doesn't exist
            LOGGER.warning(f'Source T1w JSON sidecar not found at: {source_t1w_json_path}')
        except json.JSONDecodeError as e:
            LOGGER.error(f'Error decoding JSON from {source_t1w_json_path}: {e}')
        except Exception as e:
            LOGGER.error(f'Error reading source T1w JSON {source_t1w_json_path}: {e}')
    else:
        LOGGER.warning('No source T1w JSON sidecar path provided.')

    # 2. Read volumes CSV/TSV
    volumes_dict = {}
    volumes_ordered_dict = OrderedDict() # Initialize OrderedDict
    try:
        LOGGER.info(f'Attempting to read volumes from: {volumes_csv}')
        volumes_df = pd.read_csv(volumes_csv, sep='\t')
        LOGGER.info(f'Successfully read DataFrame from {volumes_csv}. Shape: {volumes_df.shape}')
        if not volumes_df.empty:
            # Assume single row of volumes
            volume_data = volumes_df.iloc[0].to_dict()
            # Convert keys using the loaded mapping, falling back to original snake_case if no map
            volumes_dict = {}
            for orig_key, value in volume_data.items():
                snake_key = _to_snake_case(orig_key)
                # Use mapped key if available, otherwise use original snake_key
                final_key = roi_mapping.get(snake_key, snake_key)
                # Skip if the final key is explicitly None or empty after potential mapping error
                if final_key:
                    volumes_dict[final_key] = value
                else:
                    LOGGER.warning(f'Skipping volume key "{orig_key}" (snake_case: '
                    f'"{snake_key}") due to mapping issue or empty result.')

            # Create OrderedDict with 'mrid' first
            mrid_key = roi_mapping.get('mrid', 'mrid') # Get mapped mrid key, default to 'mrid'
            if mrid_key in volumes_dict:
                volumes_ordered_dict[mrid_key] = volumes_dict.pop(mrid_key)

            # Add the rest of the items, sorted alphabetically for consistency
            for key in sorted(volumes_dict.keys()):
                volumes_ordered_dict[key] = volumes_dict[key]

        else:
            # This case is problematic - indicates successful read but no data
            LOGGER.error(f'Volumes TSV/CSV file was read successfully but is empty: {volumes_csv}')
    except FileNotFoundError:
        LOGGER.error(f'Volumes input file not found: {volumes_csv}')
        raise # Reraise to ensure node failure
    except pd.errors.EmptyDataError:
        LOGGER.error(f'Pandas EmptyDataError: Volumes input file is empty: {volumes_csv}')
        raise # Reraise to ensure node failure
    except Exception as e:
        LOGGER.error(
            f'Error reading or processing volumes TSV/CSV {volumes_csv}: {e!r}'
        )  # Use !r for detailed repr
        raise # Reraise to ensure node failure

    # 3. provenance: Gather system/version info
    LOGGER.info('Gathering provenance information...') # Add log
    nichartdlmuse_version = None
    try:
        # Attempt to run NiChart_DLMUSE --version
        # Ensure NiChart_DLMUSE is callable in the execution environment
        result = subprocess.run(
            ['NiChart_DLMUSE', '--version'],
            capture_output=True,
            text=True,
            check=True,
            encoding='utf-8',
        )
        # Assuming the version is the only output, strip whitespace
        nichartdlmuse_version = result.stdout.strip()
        LOGGER.info(f'Successfully obtained NiChart_DLMUSE version: {nichartdlmuse_version}')
    except FileNotFoundError:
        LOGGER.warning('NiChart_DLMUSE command not found. Cannot determine version.')
    except subprocess.CalledProcessError as e:
        LOGGER.warning(f'NiChart_DLMUSE --version command failed: {e}. Output: {e.stderr}')
    except Exception as e:
        LOGGER.warning(f'An unexpected error occurred while getting NiChart_DLMUSE version: {e}')

    torch_version = None
    cuda_version = None
    cudnn_version = None
    try:
        torch_version = torch.__version__
        if torch.cuda.is_available():
            cuda_version = torch.version.cuda
            cudnn_version = torch.backends.cudnn.version()
            LOGGER.info(f'PyTorch: {torch_version}, CUDA: {cuda_version}, cuDNN: {cudnn_version}')
        else:
            LOGGER.info(f'PyTorch: {torch_version}, CUDA: Not available.')
            cuda_version = 'N/A'
            cudnn_version = 'N/A'
    except Exception as e:
        LOGGER.warning(f'Error getting Torch/CUDA/cuDNN versions: {e}')

    provenance = {
        'bids_ncdlmuse_version': bids_ncdlmuse_version,
        'nichartdlmuse_version': nichartdlmuse_version,
        'torch_version': torch_version,
        'cuda_version': cuda_version,
        'cudnn_version': cudnn_version,
        'device_used': device_used,
    }

    # Assemble final dictionary
    final_json_dict = {
        'bids_meta': bids_meta_dict,
        'provenance': provenance,
        'volumes': volumes_ordered_dict, # Use the OrderedDict
    }
    LOGGER.info(f'Final dictionary assembled: {list(final_json_dict.keys())}') # Log keys

    # Output filename within the node's working directory
    out_filename = 'combined_volumes_metadata.json'
    out_file_path = Path(os.getcwd()) / out_filename
    LOGGER.info(f'Attempting to write final JSON to: {out_file_path}') # Log path

    # Write the combined dictionary to the JSON file
    try:
        with open(out_file_path, 'w') as f:
            json.dump(final_json_dict, f, indent=2)
        LOGGER.info(f'Successfully wrote combined volumes JSON to {out_file_path}')
    except Exception as e:
        LOGGER.error(f'Error writing final JSON to {out_file_path}: {e}')
        raise  # Re-raise exception if writing fails

    # Return the absolute path to the created JSON file
    return str(out_file_path)

# Helper functions inspired by fMRIPrep
def _prefix(subid):
    """Ensure subject ID is prefixed with 'sub-'."""
    return subid if subid.startswith('sub-') else f'sub-{subid}'

def _make_list(item):
    """Wrap an item in a list if it's not already a list."""
    return item if isinstance(item, list) else [item]

def _select_first_from_list(file_input):
    """Select a file path, accepting either a single string or a list containing one string."""
    from pathlib import Path

    from ncdlmuse import config
    _LOGGER_INSIDE_FUNCTION = config.loggers.workflow

    path_to_check = None
    if isinstance(file_input, list):
        if file_input and file_input[0]:
            path_to_check = file_input[0]
        else:
            _LOGGER_INSIDE_FUNCTION.error(
                f'BIDSDataGrabber returned an empty list for a required field. '
                f'Received: {file_input}'
            )
            raise ValueError('BIDSDataGrabber returned an empty list for a required file.')
    elif isinstance(file_input, str | Path):
        path_to_check = file_input
    else:
        _LOGGER_INSIDE_FUNCTION.error(
            f'_select_first_from_list expected a list or a path string, '
            f'got {type(file_input)}: {file_input}'
        )
        raise ValueError(
            f'Expected a list or a path string for a required file, '
            f'got {type(file_input)}.'
        )

    if path_to_check and Path(path_to_check).exists():
        return str(path_to_check) # Ensure returning a string path

    _LOGGER_INSIDE_FUNCTION.error(
        f'BIDSDataGrabber did not return a valid existing file for a required field. '
        f'Path checked: {path_to_check}, Original input: {file_input}'
    )
    raise ValueError(
    'BIDSDataGrabber did not provide a valid existing file path for a required file.'
    )

def _select_first_from_list_or_none(file_input):
    """Select a file path if valid (from string or list), otherwise return None."""
    from pathlib import Path

    from ncdlmuse import config
    _LOGGER_INSIDE_FUNCTION = config.loggers.workflow

    path_to_check = None
    if isinstance(file_input, list):
        if file_input and file_input[0]:
            path_to_check = file_input[0]
        # If list is empty or first element is None/empty, path_to_check remains None
    elif isinstance(file_input, str | Path):
        path_to_check = file_input
    elif file_input is None: # Explicitly handle None input
        return None
    else:
        _LOGGER_INSIDE_FUNCTION.warning(
            f'_select_first_from_list_or_none expected a list, path string, or None, '
            f'got {type(file_input)}: {file_input}. Returning None.'
        )
        return None

    if path_to_check and Path(path_to_check).exists():
        return str(path_to_check) # Ensure returning a string path

    if path_to_check: # Path was provided but does not exist
        _LOGGER_INSIDE_FUNCTION.info(
            f'Optional file path from BIDSDataGrabber does not exist or was invalid: '
            f'{path_to_check}. Original input: {file_input}. Returning None.'
        )
    # If path_to_check was None from the start (e.g. empty list, or None input),
    # just return None silently for optional.
    return None

def clean_datasinks(workflow: Workflow) -> Workflow:
    """Set ``out_path_base`` to '' for all DataSinks.

    Ensures that paths are relative to the subject's derivatives directory
    as specified in the BIDS-App specification.
    This is a common pattern in NiPreps applications.
    """
    for node_name in workflow.list_node_names():
        if node_name.split('.')[-1].startswith('ds_'):
            node = workflow.get_node(node_name)
            if hasattr(node.interface, 'out_path_base'):
                node.interface.out_path_base = ''
    return workflow