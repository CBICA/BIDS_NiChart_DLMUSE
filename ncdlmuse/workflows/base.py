# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""NCDLMUSE base processing workflows."""

import json
import os
import shutil
import socket
import subprocess
import time
import warnings
from collections import OrderedDict
from importlib import resources
from pathlib import Path

import pandas as pd
import torch
from bids.layout import Query
from nipype.interfaces import utility as niu
from nipype.pipeline import engine as pe
from niworkflows.engine.workflows import LiterateWorkflow as Workflow
from niworkflows.interfaces.bids import BIDSDataGrabber
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

LOGGER = config.loggers.getLogger('ncdlmuse.workflows.base')


def init_ncdlmuse_wf(name='ncdlmuse_wf'):
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
    layout = config.execution.layout  # Get BIDS layout
    subject_list = config.execution.participant_label  # List of subjects to process
    session_list = config.execution.session_label  # Optional list of sessions
    device = config.workflow.dlmuse_device
    nthreads = config.nipype.n_procs
    model_folder = config.workflow.dlmuse_model_folder
    derived_roi_mappings_file = config.workflow.dlmuse_derived_roi_mappings_file
    muse_roi_mappings_file = config.workflow.dlmuse_muse_roi_mappings_file
    all_in_gpu = config.workflow.dlmuse_all_in_gpu
    disable_tta = config.workflow.dlmuse_disable_tta
    clear_cache = config.workflow.dlmuse_clear_cache
    save_all_outputs = config.workflow.dlmuse_save_all_outputs

    # --- Basic Workflow Setup --- #
    workflow = Workflow(name=name)
    workflow.base_dir = str(work_dir)

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
        with resources.as_file(
            resources.files('ncdlmuse.data') / 'MUSE_mapping_consecutive_indices.tsv'
        ) as p:
            mapping_tsv = str(p)
        with resources.as_file(resources.files('ncdlmuse.data') / 'io_spec.json') as p:
            io_spec = str(p)
        # Preload the ROI list TSV
        with resources.as_file(
            resources.files('ncdlmuse.data') / 'MUSE_ROI_complete_list.tsv'
        ) as p:
            roi_list_tsv = str(p)
    except FileNotFoundError as e:
        LOGGER.error(f'Required package data file not found: {e}')
        raise

    # --- Copy atlas mapping once if it doesn't exist --- #
    target_tsv_path = Path(output_dir) / 'seg-DLMUSE_dseg.tsv'  # Uses output_dir, OK
    target_json_path = Path(output_dir) / 'seg-DLMUSE_dseg.json'  # Uses output_dir, OK

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
            'return_type': 'file',
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
            except (OSError, shutil.Error) as e:
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
                    base = p.with_suffix('')  # Fallback for unexpected extensions
                sidecar = base.with_suffix('.json')
                t1w_json = str(sidecar) if sidecar.exists() else None
                if not t1w_json:
                    LOGGER.warning(f'No T1w JSON sidecar found at {sidecar}')

                # Define reportlets directory for this subject/session
                # This will be passed to init_single_subject_wf
                # The reportlets_dir should be in the derivatives directory
                reportlets_dir = (
                    Path(ncdlmuse_output_dir)
                    / f'sub-{entities.get("subject", "UNKNOWN")}'
                    / 'figures'
                )
                reportlets_dir.mkdir(parents=True, exist_ok=True)

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
                    reportlets_dir=reportlets_dir,
                    device=device,
                    nthreads=nthreads,
                    work_dir=work_dir,
                    model_folder=model_folder,
                    derived_roi_mappings_file=derived_roi_mappings_file,
                    muse_roi_mappings_file=muse_roi_mappings_file,
                    all_in_gpu=all_in_gpu,
                    disable_tta=disable_tta,
                    clear_cache=clear_cache,
                    save_all_outputs=save_all_outputs,
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
    save_all_outputs=False,
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
        (Note: Currently not used for volume name mapping, kept for backward compatibility.)
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
    save_all_outputs : bool, optional
        Save all intermediate NiChart_DLMUSE outputs.
    name : str
        Workflow name (default: 'single_subject_wf').

    Returns
    -------
    workflow : :py:class:`niworkflows.engine.workflows.LiterateWorkflow`
        The assembled Nipype workflow object for a single subject.
    """
    workflow = Workflow(name=name)  # Use LiterateWorkflow
    if work_dir:
        workflow.base_dir = str(work_dir)

    subject_id_str = _prefix(subject_id)

    # Workflow description (for reports)
    workflow.__desc__ = """\
## BIDS_NiChart_DLMUSE: BIDS App wrapper for NiChart_DLMUSE

This workflow performs deep-learning based brain extraction and segmentation using NiChart_DLMUSE
on T1-weighted (T1w) images.

Brain extraction is done using [DLICV](https://github.com/CBICA/DLICV).

Brain segmentation is done using [DLMUSE](https://github.com/CBICA/DLMUSE).

This is based on the MUSE framework (MUlti-atlas region Segmentation utilizing Ensembles of
registration algorithms and parameters, and locally optimal atlas selection) [@muse].

The results include:

* Segmented T1w in native space.
* Brain mask.
* JSON files containing ROIs' volumes.
* HTML summary for visual quality control of DLICV and DLMUSE outputs.
"""
    workflow.__postdesc__ = f"""\

For more details on the pipeline and methodologies, please consult the
[official documentation](https://github.com/CBICA/NiChart_DLMUSE).

BIDS_NiChart_DLMUSE is built using *Nipype* version {config.environment.nipype_version}
[@nipype; RRID:SCR_002502].

## References

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
            subject_data=subject_data_for_grabber, subject_id=subject_id, anat_only=True
        ),
        name='bidssrc',
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
                'volumes_json_path',  # Path to the generated JSON
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
            save_all_outputs=save_all_outputs,
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
    workflow.connect(
        [
            (bidssrc, dlmuse_node, [(('t1w', _select_first_from_list), 'input_image')]),
            (bidssrc, create_meta_node, [(('t1w', _select_first_from_list), 'raw_source_file')]),
            (
                bidssrc,
                create_seg_meta_node,
                [(('t1w', _select_first_from_list), 'raw_source_file')],
            ),
            (inputnode, create_volumes_json_node, [('roi_list_tsv', 'roi_list_tsv')]),
        ]
    )

    # Connect internal processing nodes
    workflow.connect(dlmuse_node, 'dlmuse_volumes', create_volumes_json_node, 'volumes_csv')

    # Connect processing nodes to OutputNode
    workflow.connect(dlmuse_node, 'dlmuse_segmentation', outputnode, 'dlmuse_segmentation')
    workflow.connect(dlmuse_node, 'dlicv_mask', outputnode, 'dlicv_mask')
    workflow.connect(create_meta_node, 'meta_dict', outputnode, 'brain_mask_meta')
    workflow.connect(create_volumes_json_node, 'output_json_path', outputnode, 'volumes_json_path')

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
            function=_copy_single_file,
        ),
        name='copy_volumes_json',
    )

    # --- Connect Processing Nodes to DataSinks --- #

    # Connect Inputs required by all datasinks
    # Source file for datasinks comes from BIDSDataGrabber
    workflow.connect(
        [
            (bidssrc, ds_seg_nii, [(('t1w', _select_first_from_list), 'source_file')]),
            (bidssrc, ds_brain_mask, [(('t1w', _select_first_from_list), 'source_file')]),
            (
                bidssrc,
                ds_volumes_json_pathfinder,
                [(('t1w', _select_first_from_list), 'source_file')],
            ),
            (inputnode, ds_volumes_json_pathfinder, [('io_spec', 'io_spec')]),
        ]
    )

    # a) Connect DLMUSE segmentation NIfTI
    workflow.connect(dlmuse_node, 'dlmuse_segmentation', ds_seg_nii, 'in_file')
    workflow.connect(create_seg_meta_node, 'meta_dict', ds_seg_nii, 'meta_dict')

    # b) Connect Brain mask NIfTI + metadata
    workflow.connect(dlmuse_node, 'dlicv_mask', ds_brain_mask, 'in_file')
    workflow.connect(create_meta_node, 'meta_dict', ds_brain_mask, 'meta_dict')

    # d) Connect Volumes JSON generation and copying
    workflow.connect(
        create_volumes_json_node, 'output_json_path', ds_volumes_json_pathfinder, 'in_file'
    )
    # Connect temp JSON path to copy node in_file
    workflow.connect(create_volumes_json_node, 'output_json_path', copy_json_node, 'in_file')
    # Connect pathfinder output path to copy node out_file
    workflow.connect(ds_volumes_json_pathfinder, 'out_file', copy_json_node, 'out_file')

    # --- Copy raw outputs directory if save_all_outputs is enabled --- #
    if save_all_outputs:
        copy_raw_outputs_node = pe.Node(
            niu.Function(
                input_names=['raw_outputs_dir', 'output_dir'],
                output_names=['copied_dir'],
                function=_copy_raw_outputs_dir,
            ),
            name='copy_raw_outputs',
        )
        copy_raw_outputs_node.inputs.output_dir = str(derivatives_dir)
        workflow.connect(dlmuse_node, 'raw_outputs_dir', copy_raw_outputs_node, 'raw_outputs_dir')
        LOGGER.info(
            f'[{subject_id_str}] Added node to copy raw outputs directory '
            f'when save_all_outputs=True'
        )

    # --- Add Reportlet Generation Nodes --- #
    LOGGER.info(
        f'[{subject_id_str}] Adding reportlet nodes. Reportlets will be saved to: {reportlets_dir}'
    )
    # Ensure reportlets_dir (for SVGs) is a Path and exists
    current_reportlets_dir = Path(reportlets_dir)
    current_reportlets_dir.mkdir(parents=True, exist_ok=True)

    # Get base filename from original T1w file path
    t1w_path = Path(_t1w_file_path)
    base_filename = t1w_path.stem.replace('_T1w.nii.gz', '').replace('_T1w.nii', '')

    # Reportlet for Brain Mask
    plot_brain_mask = pe.Node(
        ROIsPlot(
            colors=['#FF0000'],  # Red contour
            levels=[0.5],
            out_report=str(
                current_reportlets_dir.absolute() / f'{base_filename}_desc-brainMask_T1w.svg'
            ),
        ),
        name='plot_brain_mask',
        mem_gb=0.2,  # Slightly more memory for plotting
    )

    # Reportlet for DLMUSE Segmentation
    plot_dlmuse_seg = pe.Node(
        ROIsPlot(
            out_report=str(
                current_reportlets_dir.absolute()
                / f'{base_filename}_desc-dlmuseSegmentation_T1w.svg'
            )
        ),
        name='plot_dlmuse_seg',
        mem_gb=0.2,  # Slightly more memory for plotting
    )

    # Connect T1w (background) and masks/segmentations (foreground)
    # T1w input for plots comes from BIDSDataGrabber
    workflow.connect(
        [
            (bidssrc, plot_brain_mask, [(('t1w', _select_first_from_list), 'in_file')]),
            (dlmuse_node, plot_brain_mask, [('dlicv_mask', 'in_rois')]),
        ]
    )

    workflow.connect(
        [
            (bidssrc, plot_dlmuse_seg, [(('t1w', _select_first_from_list), 'in_file')]),
            (dlmuse_node, plot_dlmuse_seg, [('dlmuse_segmentation', 'in_rois')]),
        ]
    )

    # --- END Reportlet Generation ---

    # === HTML Report Generation ===

    # 1. Subject Summary Report (Primary HTML Summary)
    session_id_value = _current_t1w_entities.get('session')
    summary_kwargs = {'subject_id': _current_t1w_entities.get('subject', 'UNKNOWN')}
    if session_id_value is not None:
        summary_kwargs['session_id'] = session_id_value

    subject_summary_node = pe.Node(
        SubjectSummary(**summary_kwargs),
        name='subject_summary_node',
        run_without_submitting=True,
    )

    # Connect inputs to subject summary
    workflow.connect(
        [
            (bidssrc, subject_summary_node, [(('t1w', _make_list), 't1w')]),
            (
                dlmuse_node,
                subject_summary_node,
                [('dlicv_mask', 'brain_mask_file'), ('dlmuse_segmentation', 'dlmuse_seg_file')],
            ),
        ]
    )

    # Create DerivativesDataSink nodes for all HTML reports
    ds_report_summary = pe.Node(
        niu.Function(
            input_names=['in_file', 'out_dir', 'filename'],
            output_names=['out_file'],
            function=_save_file_directly,
        ),
        name='ds_report_summary',
        run_without_submitting=True,
    )

    # 2. Execution Provenance Report (About this NCDLMUSE run)
    exec_provenance_node = pe.Node(
        ExecutionProvenanceReportlet(
            pipeline_name='ncdlmuse',
            version=config.environment.version,
            command=' '.join(config.execution.cmdline),
            timestamp=time.strftime('%Y-%m-%d %H:%M:%S %Z'),
        ),
        name='execution_provenance_node',
        run_without_submitting=True,
    )

    ds_report_about = pe.Node(
        niu.Function(
            input_names=['in_file', 'out_dir', 'filename'],
            output_names=['out_file'],
            function=_save_file_directly,
        ),
        name='ds_report_about',
        run_without_submitting=True,
    )

    # 3. Error Reportlet (Checks DLMUSE outputs)
    check_dlmuse_outputs_node = pe.Node(
        niu.Function(
            input_names=['segmentation_file', 'volumes_csv_file'],
            output_names=['error_messages_list'],
            function=_check_dlmuse_outputs,
        ),
        name='check_dlmuse_outputs_node',
        run_without_submitting=True,
    )

    error_report_node = pe.Node(ErrorReportlet(), name='error_report_node')

    ds_error_report = pe.Node(
        niu.Function(
            input_names=['in_file', 'out_dir', 'filename'],
            output_names=['out_file'],
            function=_save_file_directly,
        ),
        name='ds_error_report',
        run_without_submitting=True,
    )

    # 4. Workflow Provenance Reportlet (Software versions from JSON)
    workflow_provenance_report_node = pe.Node(
        WorkflowProvenanceReportlet(), name='workflow_provenance_report_node'
    )

    ds_workflow_provenance_report = pe.Node(
        niu.Function(
            input_names=['in_file', 'out_dir', 'filename'],
            output_names=['out_file'],
            function=_save_file_directly,
        ),
        name='ds_workflow_provenance_report',
        run_without_submitting=True,
    )

    # Create input nodes for the file paths
    summary_input = pe.Node(
        niu.IdentityInterface(fields=['out_dir', 'filename']), name='summary_input'
    )
    summary_input.inputs.out_dir = str(current_reportlets_dir.absolute())
    summary_input.inputs.filename = f'{base_filename}_desc-summary_T1w.html'

    about_input = pe.Node(
        niu.IdentityInterface(fields=['out_dir', 'filename']), name='about_input'
    )
    about_input.inputs.out_dir = str(current_reportlets_dir.absolute())
    about_input.inputs.filename = f'{base_filename}_desc-about_T1w.html'

    errors_input = pe.Node(
        niu.IdentityInterface(fields=['out_dir', 'filename']), name='errors_input'
    )
    errors_input.inputs.out_dir = str(current_reportlets_dir.absolute())
    errors_input.inputs.filename = f'{base_filename}_desc-processingErrors_T1w.html'

    provenance_input = pe.Node(
        niu.IdentityInterface(fields=['out_dir', 'filename']), name='provenance_input'
    )
    provenance_input.inputs.out_dir = str(current_reportlets_dir.absolute())
    provenance_input.inputs.filename = f'{base_filename}_desc-workflowProvenance_T1w.html'

    # Connect all report nodes
    workflow.connect(
        [
            # Subject Summary Report
            (subject_summary_node, ds_report_summary, [('out_report', 'in_file')]),
            (summary_input, ds_report_summary, [('out_dir', 'out_dir'), ('filename', 'filename')]),
            # Execution Provenance Report
            (exec_provenance_node, ds_report_about, [('out_report', 'in_file')]),
            (about_input, ds_report_about, [('out_dir', 'out_dir'), ('filename', 'filename')]),
            # Error Report
            (
                dlmuse_node,
                check_dlmuse_outputs_node,
                [
                    ('dlmuse_segmentation', 'segmentation_file'),
                    ('dlmuse_volumes', 'volumes_csv_file'),
                ],
            ),
            (
                check_dlmuse_outputs_node,
                error_report_node,
                [('error_messages_list', 'error_messages')],
            ),
            (error_report_node, ds_error_report, [('out_report', 'in_file')]),
            (errors_input, ds_error_report, [('out_dir', 'out_dir'), ('filename', 'filename')]),
            # Workflow Provenance Report
            (
                create_volumes_json_node,
                workflow_provenance_report_node,
                [('output_json_path', 'provenance_json_file')],
            ),
            (
                workflow_provenance_report_node,
                ds_workflow_provenance_report,
                [('out_report', 'in_file')],
            ),
            (
                provenance_input,
                ds_workflow_provenance_report,
                [('out_dir', 'out_dir'), ('filename', 'filename')],
            ),
        ]
    )

    # === END HTML Report Generation ===

    return clean_datasinks(workflow)  # Apply clean_datasinks


# --- Helper functions for _create_volumes_json_file ---


def _check_dlmuse_outputs(segmentation_file, volumes_csv_file):
    """Check if DLMUSE outputs exist and are not empty."""
    from pathlib import Path

    error_messages = []
    if not (
        segmentation_file
        and Path(segmentation_file).exists()
        and Path(segmentation_file).stat().st_size > 0
    ):
        error_messages.append('NiChart_DLMUSE segmentation output NIfTI file is missing or empty.')
    if not (
        volumes_csv_file
        and Path(volumes_csv_file).exists()
        and Path(volumes_csv_file).stat().st_size > 0
    ):
        error_messages.append('NiChart_DLMUSE volumes output CSV file is missing or empty.')

    # If no errors were added, ensure the list is not empty & ErrorReportlet shows "no errors"
    # if not error_messages: # This logic is handled by ErrorReportlet if it receives an empty list
    #     pass # ErrorReportlet handles empty list by showing "no specific errors"
    return error_messages


def _copy_atlas_mapping(source_tsv_path_str, output_dir_str):
    """Copy the atlas mapping TSV and JSON files to the output directory if they don't exist,
    renaming them to seg-DLMUSE_dseg.*."""
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
        f'directory: {output_dir}'
    )

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

    except (OSError, shutil.Error) as e:
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


def _copy_raw_outputs_dir(raw_outputs_dir, output_dir):
    """Copy the entire raw outputs directory to the final output directory.

    This preserves all NiChart_DLMUSE raw outputs when save_all_outputs=True.
    """
    import shutil
    from pathlib import Path

    from ncdlmuse import config

    LOGGER = config.loggers.getLogger('ncdlmuse.workflows.base')

    if not raw_outputs_dir:
        return None

    raw_dir = Path(raw_outputs_dir)
    if not raw_dir.exists() or not raw_dir.is_dir():
        LOGGER.warning(f'Raw outputs directory does not exist: {raw_outputs_dir}')
        return None

    # Create destination directory in the output derivatives folder
    dest_dir = Path(output_dir) / 'raw_outputs'
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Copy the entire directory tree
    try:
        # Copy the directory contents, preserving structure
        dest_raw_dir = dest_dir / raw_dir.name
        if dest_raw_dir.exists():
            shutil.rmtree(dest_raw_dir)
        shutil.copytree(raw_dir, dest_raw_dir, dirs_exist_ok=False)
        LOGGER.info(f'Copied raw outputs directory from {raw_dir} to {dest_raw_dir}')
        return str(dest_raw_dir)
    except (OSError, shutil.Error) as e:
        LOGGER.error(f'Error copying raw outputs directory: {e}')
        return None


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


# pyright: ignore
def _create_volumes_json_file(
    volumes_csv, source_t1w_json_path, device_used, roi_list_tsv, source_file=None
):
    """Create JSON with raw T1w metadata, provenance, and volumes, writing it to a file.

    Converts volumes CSV directly to JSON using original column names from NiChart_DLMUSE.

    Parameters
    ----------
    volumes_csv : str
        Path to the volumes CSV/TSV file.
    source_t1w_json_path : str or None
        Path to the source T1w JSON sidecar file.
    device_used : str
        Device used for processing (e.g., 'cpu', 'cuda', 'mps').
    roi_list_tsv : str
        Path to ROI list TSV file (not currently used, kept for backward compatibility).
    source_file : str or None, optional
        Path to the source file.
    """

    # Imports required within the Nipype Function execution scope
    import json
    import os
    import shutil
    import socket
    import subprocess
    from collections import OrderedDict
    from pathlib import Path

    import pandas as pd
    import torch

    from ncdlmuse import __version__ as bids_ncdlmuse_version
    from ncdlmuse import config

    LOGGER = config.loggers.getLogger('ncdlmuse.workflows.base')

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
        except (OSError, json.JSONDecodeError) as e:
            LOGGER.error(f'Error reading source T1w JSON {source_t1w_json_path}: {e}')
    else:
        LOGGER.warning('No source T1w JSON sidecar path provided.')

    # 2. Read volumes CSV/TSV
    volumes_ordered_dict = OrderedDict()  # Initialize OrderedDict
    try:
        LOGGER.info(f'Attempting to read volumes from: {volumes_csv}')
        volumes_df = pd.read_csv(volumes_csv, sep='\t')
        LOGGER.info(f'Successfully read DataFrame from {volumes_csv}. Shape: {volumes_df.shape}')
        if not volumes_df.empty:
            # Preserve exact column order from CSV
            for col in volumes_df.columns:
                volumes_ordered_dict[col] = volumes_df.iloc[0][col]

        else:
            # This case is problematic - indicates successful read but no data
            LOGGER.error(f'Volumes TSV/CSV file was read successfully but is empty: {volumes_csv}')
    except FileNotFoundError:
        LOGGER.error(f'Volumes input file not found: {volumes_csv}')
        raise  # Reraise to ensure node failure
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as e:
        LOGGER.error(f'Error reading or processing volumes TSV/CSV {volumes_csv}: {e!r}')
        raise  # Reraise to ensure node failure

    # 3. provenance: Gather system/version info
    LOGGER.info('Gathering provenance information...')  # Add log
    nichartdlmuse_version = None
    try:
        # Attempt to run NiChart_DLMUSE --version
        # Use shutil.which to find the full path to NiChart_DLMUSE
        nichart_dlmuse_path = shutil.which('NiChart_DLMUSE')
        if not nichart_dlmuse_path:
            LOGGER.warning('NiChart_DLMUSE command not found in PATH. Cannot determine version.')
            nichartdlmuse_version = None
        else:
            result = subprocess.run(
                [nichart_dlmuse_path, '--version'],
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
        nichartdlmuse_version = None
    except (OSError, subprocess.SubprocessError) as e:
        LOGGER.warning(f'An unexpected error occurred while getting NiChart_DLMUSE version: {e}')
        nichartdlmuse_version = None

    torch_version = None
    cuda_version = None
    cudnn_version = None
    compute_node = None

    gpu_driver_version = None
    try:
        torch_version = torch.__version__
        if torch.cuda.is_available():
            cuda_version = torch.version.cuda
            cudnn_version = torch.backends.cudnn.version()

            # Get GPU driver information
            try:
                # Get driver version using nvidia-smi
                try:
                    nvidia_smi_path = shutil.which('nvidia-smi')
                    if nvidia_smi_path is None:
                        raise FileNotFoundError('nvidia-smi not found in PATH')
                    result = subprocess.run(
                        [
                            nvidia_smi_path,
                            '--query-gpu=driver_version',
                            '--format=csv,noheader,nounits',
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                        encoding='utf-8',
                    )
                    gpu_driver_version = result.stdout.strip()
                except (FileNotFoundError, subprocess.SubprocessError):
                    LOGGER.warning('nvidia-smi not available, cannot get GPU driver version')
                    gpu_driver_version = 'N/A'

                LOGGER.info(
                    f'PyTorch: {torch_version}, CUDA: {cuda_version}, cuDNN: {cudnn_version}'
                )
                LOGGER.info(f'GPU Driver: {gpu_driver_version}')
            except (OSError, RuntimeError) as gpu_e:
                LOGGER.warning(f'Error getting GPU hardware info: {gpu_e}')
                gpu_driver_version = 'N/A'
        else:
            LOGGER.info(f'PyTorch: {torch_version}, CUDA: Not available.')
            cuda_version = 'N/A'
            cudnn_version = 'N/A'
            gpu_driver_version = 'N/A'
    except (OSError, subprocess.SubprocessError) as e:
        LOGGER.warning(f'Error getting Torch/CUDA/cuDNN versions: {e}')
        gpu_driver_version = 'N/A'

    # Get compute node name from SLURM environment variables
    # Priority: SLURMD_NODENAME (most specific) > SLURM_NODELIST > SLURM_JOB_NODELIST > hostname
    if os.getenv('SLURMD_NODENAME'):
        compute_node = os.getenv('SLURMD_NODENAME')
        LOGGER.info(f'Compute node from SLURMD_NODENAME: {compute_node}')
    elif os.getenv('SLURM_NODELIST'):
        compute_node = os.getenv('SLURM_NODELIST')
        LOGGER.info(f'Compute node from SLURM_NODELIST: {compute_node}')
    elif os.getenv('SLURM_JOB_NODELIST'):
        compute_node = os.getenv('SLURM_JOB_NODELIST')
        LOGGER.info(f'Compute node from SLURM_JOB_NODELIST: {compute_node}')
    else:
        # Fallback to system hostname if SLURM variables are not available
        try:
            compute_node = socket.gethostname()
            LOGGER.info(f'Compute node from hostname: {compute_node}')
        except OSError as e:
            compute_node = 'N/A'
            LOGGER.warning(f'Could not determine compute node (hostname lookup failed: {e})')

    provenance = {
        'bids_ncdlmuse_version': bids_ncdlmuse_version,
        'nichartdlmuse_version': nichartdlmuse_version,
        'torch_version': torch_version,
        'cuda_version': cuda_version,
        'cudnn_version': cudnn_version,
        'device_used': device_used,
        'compute_node': compute_node,
        'gpu_driver_version': gpu_driver_version,
    }

    # Assemble final dictionary
    final_json_dict = {
        'bids_meta': bids_meta_dict,
        'provenance': provenance,
        'volumes': volumes_ordered_dict,  # Use the OrderedDict
    }
    LOGGER.info(f'Final dictionary assembled: {list(final_json_dict.keys())}')  # Log keys

    # Output filename within the node's working directory
    out_filename = 'combined_volumes_metadata.json'
    out_file_path = Path(os.getcwd()) / out_filename
    LOGGER.info(f'Attempting to write final JSON to: {out_file_path}')  # Log path

    # Write the combined dictionary to the JSON file
    try:
        with open(out_file_path, 'w') as f:
            json.dump(final_json_dict, f, indent=2)
        LOGGER.info(f'Successfully wrote combined volumes JSON to {out_file_path}')
    except (OSError, json.JSONDecodeError) as e:
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

    _LOGGER_INSIDE_FUNCTION = config.loggers.getLogger('ncdlmuse.workflows.base')

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
            f'Expected a list or a path string for a required file, got {type(file_input)}.'
        )

    if path_to_check and Path(path_to_check).exists():
        return str(path_to_check)  # Ensure returning a string path

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

    _LOGGER_INSIDE_FUNCTION = config.loggers.getLogger('ncdlmuse.workflows.base')

    path_to_check = None
    if isinstance(file_input, list):
        if file_input and file_input[0]:
            path_to_check = file_input[0]
        # If list is empty or first element is None/empty, path_to_check remains None
    elif isinstance(file_input, str | Path):
        path_to_check = file_input
    elif file_input is None:  # Explicitly handle None input
        return None
    else:
        _LOGGER_INSIDE_FUNCTION.warning(
            f'_select_first_from_list_or_none expected a list, path string, or None, '
            f'got {type(file_input)}: {file_input}. Returning None.'
        )
        return None

    if path_to_check and Path(path_to_check).exists():
        return str(path_to_check)  # Ensure returning a string path

    if path_to_check:  # Path was provided but does not exist
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


# --- Add helper function for direct file saving ---
def _save_file_directly(in_file, out_dir, filename):
    """Save a file directly to the specified directory with the given filename.
    This bypasses the DerivativesDataSink's directory creation and path manipulation.
    """
    import shutil
    from pathlib import Path

    # Create output directory if it doesn't exist
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Create output file path
    out_file = out_path / filename

    # Copy the file
    shutil.copy2(in_file, out_file)

    # Return the output file path
    return str(out_file)
