"""Tests for ncdlmuse interfaces."""

from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from ncdlmuse.interfaces.ncdlmuse import NiChartDLMUSE


@pytest.fixture(scope='function')
def synthetic_t1w_file(tmp_path):
    """Creates just the synthetic T1w file."""
    t1w_data = np.zeros((10, 10, 10), dtype=np.float32)
    t1w_data[4:6, 4:6, 4:6] = 1.0
    t1w_affine = np.eye(4)
    t1w_img = nib.Nifti1Image(t1w_data, t1w_affine)
    t1w_filename = tmp_path / 'synth_T1w.nii.gz'
    t1w_img.to_filename(t1w_filename)
    return str(t1w_filename)

@pytest.mark.parametrize(
    ('inputs', 'expected_args'),
    [
        # Default case (cpu)
        ({}, ['-d', 'cpu']),
        # CUDA device
        ({'device': 'cuda'}, ['-d', 'cuda']),
        # Disable TTA
        ({'disable_tta': True}, ['-d', 'cpu', '--disable_tta']),
        # Clear cache
        ({'clear_cache': True}, ['-d', 'cpu', '--clear_cache']),
        # All in GPU
        ({'all_in_gpu': True}, ['-d', 'cpu', '--all_in_gpu']),
        # Model folder
        ({'model_folder': '/path/models'}, ['-d', 'cpu', '--model_folder', '/path/models']),
        # All options together
        ({'device': 'cuda', 'disable_tta': True, 'clear_cache': True, 'all_in_gpu': True},
         ['-d', 'cuda', '--all_in_gpu', '--disable_tta', '--clear_cache']),
    ]
)
def test_nichartdlmuse_cmdline(synthetic_t1w_file, inputs, expected_args):
    """Check that the interface generates the correct command line arguments."""
    iface = NiChartDLMUSE(
        input_image=synthetic_t1w_file,
        **inputs
    )

    # The cmdline attribute holds the command string
    cmd = iface.cmdline

    # Basic checks
    assert cmd.startswith('NiChart_DLMUSE')
    assert f'-i {Path(synthetic_t1w_file).parent}' in cmd # Checks input dir based on file
    assert '-o ' in cmd # Check that output dir flag exists

    # Check for expected optional args (order might vary slightly)
    for arg in expected_args:
        if ' ' in arg: # Handle args with values like '--model_folder /path'
            assert arg in cmd
        else: # Handle flags like '--disable_tta'
            assert f' {arg}' in cmd

    # Check that args not provided are NOT in the cmdline (example)
    if not inputs.get('disable_tta', False):
        assert '--disable_tta' not in cmd


# NOTE: Testing the actual execution of the interface requires either:
# 1. A mock/dummy NiChart_DLMUSE executable in the test environment's PATH.
# 2. Running in an environment where the real executable and its dependencies
#    (models, specific GPU libraries if testing cuda) are available.
# This is often handled in integration tests rather than pure unit tests.

def test_nichartdlmuse_missing_input():
    """Test that the interface raises an error if input_image is missing."""
    with pytest.raises( ValueError,match="NiChartDLMUSE requires a value for input 'input_image'"):
        NiChartDLMUSE().run()
