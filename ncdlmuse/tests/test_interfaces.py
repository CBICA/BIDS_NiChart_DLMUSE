"""Tests for ncdlmuse interfaces."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import nibabel as nib
import numpy as np
import pytest
from traits.api import TraitError

from ncdlmuse.interfaces.ncdlmuse import NiChartDLMUSE


@pytest.fixture
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
    ('inputs', 'expected_device'),
    [
        # Default case (cpu)
        ({}, 'cpu'),
        # CUDA device
        ({'device': 'cuda'}, 'cuda'),
        # MPS device
        ({'device': 'mps'}, 'mps'),
    ],
)
def test_nichartdlmuse_device_setting(synthetic_t1w_file, inputs, expected_device):
    """Check that the interface correctly sets the device parameter."""
    iface = NiChartDLMUSE(input_image=synthetic_t1w_file, **inputs)

    # Check that the device input is correctly set
    assert iface.inputs.device == expected_device


@pytest.mark.parametrize(
    ('inputs', 'expected_flags'),
    [
        # Default case (no flags)
        ({}, {}),
        # Disable TTA
        ({'disable_tta': True}, {'disable_tta': True}),
        # Clear cache
        ({'clear_cache': True}, {'clear_cache': True}),
        # All in GPU
        ({'all_in_gpu': True}, {'all_in_gpu': True}),
        # All flags together
        (
            {'disable_tta': True, 'clear_cache': True, 'all_in_gpu': True},
            {'disable_tta': True, 'clear_cache': True, 'all_in_gpu': True},
        ),
    ],
)
def test_nichartdlmuse_boolean_flags(synthetic_t1w_file, inputs, expected_flags):
    """Check that the interface correctly sets boolean flag parameters."""
    iface = NiChartDLMUSE(input_image=synthetic_t1w_file, **inputs)

    # Check that boolean flags are correctly set
    for flag, expected_value in expected_flags.items():
        assert getattr(iface.inputs, flag) == expected_value

    # Check that unset flags default to False
    all_flags = ['disable_tta', 'clear_cache', 'all_in_gpu']
    for flag in all_flags:
        if flag not in expected_flags:
            assert not getattr(iface.inputs, flag)


def test_nichartdlmuse_string_inputs(synthetic_t1w_file):
    """Test that string inputs are properly handled."""
    model_folder = '/path/to/models'
    derived_roi = '/path/to/derived.csv'
    muse_roi = '/path/to/muse.csv'

    iface = NiChartDLMUSE(
        input_image=synthetic_t1w_file,
        model_folder=model_folder,
        derived_roi_mappings_file=derived_roi,
        muse_roi_mappings_file=muse_roi,
    )

    assert iface.inputs.model_folder == model_folder
    assert iface.inputs.derived_roi_mappings_file == derived_roi
    assert iface.inputs.muse_roi_mappings_file == muse_roi


@patch('ncdlmuse.interfaces.ncdlmuse.subprocess.run')
def test_nichartdlmuse_command_construction(mock_subprocess, synthetic_t1w_file, tmp_path):
    """Test that the correct command is constructed internally."""
    # Mock successful subprocess execution
    mock_subprocess.return_value.returncode = 0
    mock_subprocess.return_value.stdout = 'Success'
    mock_subprocess.return_value.stderr = ''

    # Mock the runtime object
    runtime = MagicMock()
    runtime.cwd = str(tmp_path / 'work')
    Path(runtime.cwd).mkdir(exist_ok=True)

    iface = NiChartDLMUSE(
        input_image=synthetic_t1w_file, device='cuda', disable_tta=True, clear_cache=True
    )

    # This will fail due to missing output files, but we can check the command
    try:
        iface._run_interface(runtime)
    except (RuntimeError, FileNotFoundError):
        # Expected to fail due to missing output files or command not found
        pass

    # Check that subprocess was called with correct command
    assert mock_subprocess.called
    called_cmd = mock_subprocess.call_args[0][0]

    # Verify command structure
    assert called_cmd[0] == 'NiChart_DLMUSE'
    assert '-d' in called_cmd
    assert 'cuda' in called_cmd
    assert '--disable_tta' in called_cmd
    assert '--clear_cache' in called_cmd


def test_nichartdlmuse_missing_input():
    """Test that the interface raises an error if input_image is missing."""
    with pytest.raises(ValueError, match="NiChartDLMUSE requires a value for input 'input_image'"):
        NiChartDLMUSE().run()


def test_nichartdlmuse_input_validation(synthetic_t1w_file):
    """Test input validation for the interface."""
    # Valid construction should work
    iface = NiChartDLMUSE(input_image=synthetic_t1w_file)
    assert iface.inputs.input_image == synthetic_t1w_file

    # Invalid device should raise error during validation
    with pytest.raises(
        TraitError,
        match=(
            "The 'device' trait of a NiChartDLMUSEInputSpec instance must be "
            "'cpu' or 'cuda' or 'mps'"
        ),
    ):
        NiChartDLMUSE(input_image=synthetic_t1w_file, device='invalid_device')


def test_nichartdlmuse_output_spec():
    """Test that the output specification is correctly defined."""
    iface = NiChartDLMUSE()

    # Check that output spec has expected attributes
    expected_outputs = [
        'dlmuse_segmentation',
        'dlicv_mask',
        'dlmuse_volumes',
        'dlmuse_volumes_csv',
    ]

    for output in expected_outputs:
        assert hasattr(iface.output_spec(), output)
