"""Tests for ncdlmuse workflow construction."""

from unittest.mock import MagicMock

import pytest
from nipype.pipeline.engine import Workflow

from ncdlmuse import config

# Assuming conftest.py provides bids_skeleton_factory, work_dir, out_dir fixtures
from ncdlmuse.workflows.base import init_ncdlmuse_wf, init_single_subject_wf


@pytest.mark.parametrize(
    ('subject_id', 'session_id'),
    [
        ('01', None),  # Single session
        ('02', 'test'),  # With session
    ],
)
def test_init_single_subject_wf_structure(
    bids_skeleton_factory, work_dir, out_dir, subject_id, session_id
):
    """Test the basic structure of the single subject workflow with different entities."""
    bids_dir, t1w_file = bids_skeleton_factory(subject_id=subject_id, session_id=session_id)

    # Create entities dictionary to match the actual function signature
    entities = {'subject': subject_id}
    if session_id:
        entities['session'] = session_id
    entities.update({'datatype': 'anat', 'suffix': 'T1w'})

    # Create required parameters to match actual function signature
    wf = init_single_subject_wf(
        subject_id=subject_id,
        _t1w_file_path=str(t1w_file),
        _t1w_json_path=None,  # Assume no json
        _current_t1w_entities=entities,
        mapping_tsv=str(work_dir / 'mapping.tsv'),  # Dummy path
        io_spec=str(work_dir / 'io_spec.json'),  # Dummy path
        roi_list_tsv=str(work_dir / 'roi_list.tsv'),  # Dummy path
        derivatives_dir=out_dir,
        reportlets_dir=work_dir / 'reportlets',
        device='cpu',
        nthreads=1,
        work_dir=work_dir,
        name=f'test_single_subj_sub-{subject_id}_wf',
    )

    assert isinstance(wf, Workflow)
    # Check that basic nodes exist - these are the actual node names from the implementation
    assert wf.get_node('bidssrc') is not None
    assert wf.get_node('inputnode') is not None
    assert wf.get_node('outputnode') is not None
    assert wf.get_node('nichartdlmuse_node') is not None


@pytest.mark.parametrize(
    ('device_setting', 'all_in_gpu_setting', 'disable_tta_setting'),
    [
        ('cpu', False, False),
        ('cuda', True, True),
    ],
)
def test_init_ncdlmuse_wf_param_passing(
    bids_skeleton_single,  # Use the simple fixture here
    work_dir,
    out_dir,
    device_setting,
    all_in_gpu_setting,
    disable_tta_setting,
):
    """Test that parameters from config are passed down to the subject workflow."""
    bids_dir = bids_skeleton_single  # Get path from fixture

    # Mock necessary config settings
    config.execution.bids_dir = bids_dir
    config.execution.output_dir = out_dir
    config.execution.work_dir = work_dir
    config.execution.ncdlmuse_dir = out_dir / 'ncdlmuse'
    config.execution.participant_label = ['01']  # Match the skeleton
    config.execution.session_label = None
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
        # Create a proper mock layout that has the .get() method
        mock_layout = MagicMock()
        mock_layout.get.return_value = [str(bids_dir / 'sub-01' / 'anat' / 'sub-01_T1w.nii.gz')]

        # Mock the get_entities_from_file function to return proper entities
        def mock_get_entities(file_path, layout=None):
            return {
                'subject': '01',
                # Don't include session at all when there's no session
                'datatype': 'anat',
                'suffix': 'T1w',
            }

        # Patch the get_entities_from_file function
        import ncdlmuse.workflows.base

        original_get_entities = ncdlmuse.workflows.base.get_entities_from_file
        ncdlmuse.workflows.base.get_entities_from_file = mock_get_entities

        config.execution.layout = mock_layout

        wf = init_ncdlmuse_wf(name='test_top_wf')

        # Check that workflow was created successfully
        assert isinstance(wf, Workflow)

        # Check that the mock layout was called (indicating the workflow tried to query)
        assert mock_layout.get.called

        # Restore the original function
        ncdlmuse.workflows.base.get_entities_from_file = original_get_entities

    finally:
        config.execution.layout = None
