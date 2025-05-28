"""Fixtures for ncdlmuse tests."""

import json

import nibabel as nib
import numpy as np
import pytest

# --- Fixtures for CLI options (if needed, copied from previous context) ---
# def pytest_addoption(parser): ...
# @pytest.fixture(scope='session') def data_dir(request): ...
# @pytest.fixture(scope='session') def working_dir(request): ...
# @pytest.fixture(scope='session') def output_dir(request): ...
# --- End CLI option fixtures ---


@pytest.fixture(autouse=True)
def reset_config():
    """Reset the global config before each test to prevent state leakage."""
    from ncdlmuse import config

    # Reset execution config
    config.execution.participant_label = None
    config.execution.session_label = None
    config.execution.layout = None
    config.execution.bids_dir = None
    config.execution.output_dir = None
    config.execution.work_dir = None
    config.execution.ncdlmuse_dir = None
    config.execution.log_dir = None
    config.execution.skip_bids_validation = False
    config.execution.t1w_list = None

    # Reset workflow config
    config.workflow.dlmuse_device = None
    config.workflow.dlmuse_model_folder = None
    config.workflow.dlmuse_derived_roi_mappings_file = None
    config.workflow.dlmuse_muse_roi_mappings_file = None
    config.workflow.dlmuse_all_in_gpu = False
    config.workflow.dlmuse_disable_tta = False
    config.workflow.dlmuse_clear_cache = False

    # Reset nipype config
    config.nipype.n_procs = 1
    config.nipype.omp_nthreads = None

    yield  # Run the test

    # Clean up after test (optional, but good practice)
    config.execution.participant_label = None
    config.execution.session_label = None
    config.execution.layout = None


def _generate_bids_skeleton(base_path, subject_id='01', session_id=None):
    """Helper function to create a BIDS skeleton."""
    bids_dir = base_path / 'bids_root'
    sub_anat_dir = bids_dir / f'sub-{subject_id}'
    if session_id:
        sub_anat_dir = sub_anat_dir / f'ses-{session_id}'
    sub_anat_dir = sub_anat_dir / 'anat'
    sub_anat_dir.mkdir(parents=True, exist_ok=True)  # Use exist_ok

    # Create synthetic T1w data
    t1w_data = np.zeros((10, 10, 10), dtype=np.float32)
    t1w_data[4:6, 4:6, 4:6] = 1.0
    t1w_affine = np.eye(4)
    t1w_img = nib.Nifti1Image(t1w_data, t1w_affine)

    t1w_basename = f'sub-{subject_id}'
    if session_id:
        t1w_basename += f'_ses-{session_id}'
    t1w_basename += '_T1w.nii.gz'
    t1w_filename = sub_anat_dir / t1w_basename
    t1w_img.to_filename(t1w_filename)

    # Create dataset_description.json if it doesn't exist
    desc_filename = bids_dir / 'dataset_description.json'
    if not desc_filename.exists():
        dataset_desc = {
            'Name': 'NCDLMUSE Test Skeleton',
            'BIDSVersion': '1.10.0',
            'DatasetType': 'raw',
            'Authors': ['pytest'],
        }
        with open(desc_filename, 'w') as f:
            json.dump(dataset_desc, f, indent=2)

    # Create or append to participants.tsv
    participants_tsv = bids_dir / 'participants.tsv'
    if not participants_tsv.exists():
        participants_tsv.write_text('participant_id\tage\n')
    with open(participants_tsv, 'a') as f:
        # Simple age placeholder, could be parametrized too
        f.write(f'sub-{subject_id}\t25\n')

    return bids_dir, t1w_filename  # Return both root and the image path


@pytest.fixture
def bids_skeleton_factory(tmp_path):
    """Provides a factory function to generate BIDS skeletons."""

    def _factory(subject_id='01', session_id=None):
        return _generate_bids_skeleton(tmp_path, subject_id, session_id)

    return _factory


@pytest.fixture
def bids_skeleton_single(bids_skeleton_factory):
    """Provides a default single-subject, single-session BIDS skeleton."""
    bids_dir, _ = bids_skeleton_factory(subject_id='01')
    return bids_dir  # Keep original fixture name for compatibility if needed


@pytest.fixture
def work_dir(tmp_path):
    """Provides a temporary working directory path."""
    work_path = tmp_path / 'work'
    work_path.mkdir(exist_ok=True)  # Use exist_ok
    return work_path  # Return Path object


@pytest.fixture
def out_dir(tmp_path):
    """Provides a temporary output directory path."""
    out_path = tmp_path / 'out'
    out_path.mkdir(exist_ok=True)  # Use exist_ok
    return out_path  # Return Path object
