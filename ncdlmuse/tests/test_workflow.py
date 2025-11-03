"""Tests for ncdlmuse workflow construction."""

from unittest.mock import MagicMock, patch

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


def test_gpu_hardware_detection():
    """Test GPU hardware detection and compute node in provenance collection."""
    import json
    import os
    import tempfile
    from pathlib import Path

    from ncdlmuse.workflows.base import _create_volumes_json_file

    # Create a temporary CSV file for testing
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write('ROI_ID,Volume\n1,1000\n2,2000\n')
        temp_csv = f.name

    try:
        # Test with mocked GPU detection and SLURM node
        with (
            patch('torch.cuda.is_available', return_value=True),
            patch('shutil.which', return_value='/usr/bin/nvidia-smi'),  # Mock nvidia-smi path
            patch('subprocess.run') as mock_subprocess,
            patch.dict(os.environ, {'SLURMD_NODENAME': '211affn012'}),
        ):
            # Mock nvidia-smi output
            mock_result = MagicMock()
            mock_result.stdout = '525.85.12\n'
            mock_subprocess.return_value = mock_result

            # Mock torch version info
            with (
                patch('torch.__version__', '2.3.1'),
                patch('torch.version.cuda', '12.1'),
                patch('torch.backends.cudnn.version', return_value=8902),
            ):
                # Create a temporary output file
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as json_f:
                    temp_json = json_f.name

                try:
                    # Call the function
                    result_path = _create_volumes_json_file(
                        volumes_csv=temp_csv,
                        source_t1w_json_path=None,
                        device_used='cuda',
                        roi_list_tsv=None,
                        source_file=None,
                    )

                    # Read the result and check GPU info
                    with open(result_path) as f:
                        result_data = json.load(f)

                    provenance = result_data.get('provenance', {})
                    assert 'compute_node' in provenance
                    assert 'gpu_driver_version' in provenance
                    assert provenance['compute_node'] == '211affn012'
                    assert provenance['gpu_driver_version'] == '525.85.12'
                    assert provenance['device_used'] == 'cuda'

                finally:
                    # Clean up temporary files
                    Path(temp_json).unlink(missing_ok=True)
                    Path(result_path).unlink(missing_ok=True)

    finally:
        # Clean up temporary CSV
        Path(temp_csv).unlink(missing_ok=True)


def test_gpu_hardware_detection_cpu():
    """Test GPU hardware detection when CUDA is not available."""
    import json
    import os
    import tempfile
    from pathlib import Path

    from ncdlmuse.workflows.base import _create_volumes_json_file

    # Create a temporary CSV file for testing
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write('ROI_ID,Volume\n1,1000\n2,2000\n')
        temp_csv = f.name

    try:
        # Test with CUDA not available and no SLURM env vars
        env_backup = os.environ.copy()
        try:
            # Remove SLURM vars if they exist
            for key in ['SLURMD_NODENAME', 'SLURM_NODELIST', 'SLURM_JOB_NODELIST']:
                os.environ.pop(key, None)

            with (
                patch('torch.cuda.is_available', return_value=False),
                patch('torch.__version__', '2.3.1'),
            ):
                # Create a temporary output file
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as json_f:
                    temp_json = json_f.name

                try:
                    # Call the function
                    result_path = _create_volumes_json_file(
                        volumes_csv=temp_csv,
                        source_t1w_json_path=None,
                        device_used='cpu',
                        roi_list_tsv=None,
                        source_file=None,
                    )

                    # Read the result and check GPU info
                    with open(result_path) as f:
                        result_data = json.load(f)

                    provenance = result_data.get('provenance', {})
                    assert 'compute_node' in provenance
                    assert 'gpu_driver_version' in provenance
                    assert provenance['compute_node'] == 'N/A'
                    assert provenance['gpu_driver_version'] == 'N/A'
                    assert provenance['device_used'] == 'cpu'

                finally:
                    # Clean up temporary files
                    Path(temp_json).unlink(missing_ok=True)
                    Path(result_path).unlink(missing_ok=True)
        finally:
            # Restore environment
            os.environ.clear()
            os.environ.update(env_backup)

    finally:
        # Clean up temporary CSV
        Path(temp_csv).unlink(missing_ok=True)
