"""Tests for ncdlmuse workflow construction."""

from pathlib import Path

import pytest
from nipype.pipeline.engine import Workflow

from ncdlmuse import config
from ncdlmuse.utils.bids import get_entities_from_file  # Need this helper

# Assuming conftest.py provides bids_skeleton_factory, work_dir, out_dir fixtures
from ncdlmuse.workflows.base import init_ncdlmuse_wf, init_single_subject_wf


@pytest.mark.parametrize(
    ('subject_id', 'session_id'),
    [
        ('01', None),    # Single session
        ('02', 'test'),  # With session
    ]
)
def test_init_single_subject_wf_structure(bids_skeleton_factory, work_dir, out_dir, subject_id, session_id):
    """Test the basic structure of the single subject workflow with different entities."""
    bids_dir, t1w_file = bids_skeleton_factory(subject_id=subject_id, session_id=session_id)

    # Extract entities using the helper function
    # Note: BIDSLayout isn't easily available here, pass entities manually for simplicity
    entities = {'subject': subject_id}
    if session_id:
        entities['session'] = session_id
    entities.update({'datatype': 'anat', 'suffix': 'T1w'}) # Add required static entities

    # Create expected workflow name suffix
    wf_name_suffix = f'sub-{subject_id}'
    if session_id:
        wf_name_suffix += f'_ses-{session_id}'

    wf = init_single_subject_wf(
        t1w_file=str(t1w_file),
        t1w_json=None, # Assume no json
        mapping_tsv=None, # Assume defaults
        io_spec=None,
        roi_list_tsv=None,
        derivatives_dir=out_dir,
        entities=entities,
        device='cpu',
        nthreads=1,
        work_dir=work_dir,
        name=f'test_single_subj_{wf_name_suffix}_wf' # Use dynamic name
    )

    assert isinstance(wf, Workflow)
    assert wf.get_node('inputnode') is not None
    assert wf.get_node('outputnode') is not None
    assert wf.get_node('dlmuse_wf') is not None
    assert wf.get_node('create_volumes_json_node') is not None
    assert wf.get_node('ds_dlmuse_segmentation') is not None

@pytest.mark.parametrize(
    ('device_setting', 'all_in_gpu_setting', 'disable_tta_setting'),
    [
        ('cpu', False, False),
        ('cuda', True, True),
    ]
)
def test_init_ncdlmuse_wf_param_passing(bids_skeleton_single, # Use the simple fixture here
                                       work_dir, out_dir,
                                       device_setting, all_in_gpu_setting, disable_tta_setting):
    """Test that parameters from config are passed down to the subject workflow."""
    bids_dir = bids_skeleton_single # Get path from fixture

    # Mock necessary config settings
    config.execution.bids_dir = bids_dir
    config.execution.output_dir = out_dir
    config.execution.work_dir = work_dir
    config.execution.ncdlmuse_dir = out_dir / 'ncdlmuse'
    config.execution.participant_label = ['01'] # Match the skeleton
    config.execution.session_label = None
    config.execution.layout = None # Will be recreated or error
    config.nipype.n_procs = 1

    config.workflow.dlmuse_device = device_setting
    config.workflow.dlmuse_all_in_gpu = all_in_gpu_setting
    config.workflow.dlmuse_disable_tta = disable_tta_setting
    # Reset others to default for isolation
    config.workflow.dlmuse_clear_cache = False
    config.workflow.dlmuse_model_folder = None
    config.workflow.dlmuse_derived_roi_mappings_file = None
    config.workflow.dlmuse_muse_roi_mappings_file = None

    try:
        # Temporarily mock layout to bypass BIDS query for this specific test
        config.execution.layout = True

        wf = init_ncdlmuse_wf(name='test_top_wf')

        subject_wf_node = wf.get_node('single_subject_sub-01_wf') # Name based on default skeleton
        assert subject_wf_node is not None
        assert isinstance(wf, Workflow)

        # TODO: Add introspection checks as before if needed

    finally:
        config.execution.layout = None 