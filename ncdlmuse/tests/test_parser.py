"""Test parser."""

import pytest
from packaging.version import Version

from ncdlmuse import config
from ncdlmuse.cli import version as _version
from ncdlmuse.cli.parser import _build_parser, parse_args

MIN_ARGS = ['data/', 'out/', 'participant']


@pytest.mark.parametrize(
    ('args', 'code'),
    [
        ([], 2),
        (MIN_ARGS, 2),  # bids_dir does not exist
        (MIN_ARGS + ['--fs-license-file'], 2),
        (MIN_ARGS + ['--fs-license-file', 'fslicense.txt'], 2),
    ],
)
def test_parser_errors(args, code):
    """Check behavior of the parser."""
    with pytest.raises(SystemExit) as error:
        _build_parser().parse_args(args)

    assert error.value.code == code


@pytest.mark.parametrize('args', [MIN_ARGS, MIN_ARGS + ['--fs-license-file']])
def test_parser_valid(tmp_path, args):
    """Check valid arguments."""
    datapath = tmp_path / 'data'
    datapath.mkdir(exist_ok=True)
    args[0] = str(datapath)

    if '--fs-license-file' in args:
        _fs_file = tmp_path / 'license.txt'
        _fs_file.write_text('')
        args.insert(args.index('--fs-license-file') + 1, str(_fs_file.absolute()))

    opts = _build_parser().parse_args(args)

    assert opts.bids_dir == datapath


@pytest.mark.parametrize(
    ('argval', 'gb'),
    [
        ('1G', 1),
        ('1GB', 1),
        ('1000', 1),  # Default units are MB
        ('32000', 32),  # Default units are MB
        ('4000', 4),  # Default units are MB
        ('1000M', 1),
        ('1000MB', 1),
        ('1T', 1000),
        ('1TB', 1000),
        ('%dK' % 1e6, 1),
        ('%dKB' % 1e6, 1),
        ('%dB' % 1e9, 1),
    ],
)
def test_memory_arg(tmp_path, argval, gb):
    """Check the correct parsing of the memory argument."""
    datapath = tmp_path / 'data'
    datapath.mkdir(exist_ok=True)
    _fs_file = tmp_path / 'license.txt'
    _fs_file.write_text('')

    args = MIN_ARGS + ['--fs-license-file', str(_fs_file)] + ['--mem', argval]
    opts = _build_parser().parse_args(args)

    assert opts.memory_gb == gb


@pytest.mark.parametrize(('current', 'latest'), [('1.0.0', '1.3.2'), ('1.3.2', '1.3.2')])
def test_get_parser_update(monkeypatch, capsys, current, latest):
    """Make sure the out-of-date banner is shown."""
    expectation = Version(current) < Version(latest)

    def _mock_check_latest(*args, **kwargs):
        return Version(latest)

    monkeypatch.setattr(config.environment, 'version', current)
    monkeypatch.setattr(_version, 'check_latest', _mock_check_latest)

    _build_parser()
    captured = capsys.readouterr().err

    msg = f"""\
You are using ncdlmuse-{current}, and a newer version of ncdlmuse is available: {latest}."""

    assert (msg in captured) is expectation


@pytest.mark.parametrize('flagged', [(True, None), (True, 'random reason'), (False, None)])
def test_get_parser_blacklist(monkeypatch, capsys, flagged):
    """Make sure the blacklisting banner is shown."""

    def _mock_is_bl(*args, **kwargs):
        return flagged

    monkeypatch.setattr(_version, 'is_flagged', _mock_is_bl)

    _build_parser()
    captured = capsys.readouterr().err

    assert ('FLAGGED' in captured) is flagged[0]
    if flagged[0]:
        assert (flagged[1] or 'reason: unknown') in captured


@pytest.mark.parametrize(
    ('arg_list', 'config_section', 'config_key', 'expected_value'),
    [
        # Test default device
        (MIN_ARGS, 'workflow', 'dlmuse_device', 'cpu'),
        # Test explicit device
        ([*MIN_ARGS, '--device=cuda'], 'workflow', 'dlmuse_device', 'cuda'),
        # Test boolean flags (default False)
        (MIN_ARGS, 'workflow', 'dlmuse_disable_tta', False),
        ([*MIN_ARGS, '--disable-tta'], 'workflow', 'dlmuse_disable_tta', True),
        (MIN_ARGS, 'workflow', 'dlmuse_clear_cache', False),
        ([*MIN_ARGS, '--clear-cache'], 'workflow', 'dlmuse_clear_cache', True),
        (MIN_ARGS, 'workflow', 'dlmuse_all_in_gpu', False),
        ([*MIN_ARGS, '--all-in-gpu'], 'workflow', 'dlmuse_all_in_gpu', True),
        # Test string arguments
        ([*MIN_ARGS, '--model-folder=/custom/model'], 'workflow', 'dlmuse_model_folder', '/custom/model'),
        # Test participant label
        ([*MIN_ARGS, '--participant-label', '01', '02'], 'execution', 'participant_label', ['01', '02']),
        # Test BIDS validation skipping
        (MIN_ARGS, 'execution', 'skip_bids_validation', False),
        ([*MIN_ARGS, '--skip-bids-validation'], 'execution', 'skip_bids_validation', True),
        # Test resource limits (example)
        ([*MIN_ARGS, '--nthreads=4'], 'nipype', 'n_procs', 4),
    ]
)
def test_parser_arguments(arg_list, config_section, config_key, expected_value):
    """Test parsing of various command line arguments."""
    # We don't need actual directories for parser tests
    # Add dummy paths if Path validation is strict
    processed_args = []
    for arg in arg_list:
        if arg == 'data/':
            processed_args.append('/tmp/data') # Use dummy path
        elif arg == 'out/':
            processed_args.append('/tmp/out') # Use dummy path
        else:
            processed_args.append(arg)

    parse_args(processed_args) # This populates the global config

    # Retrieve the section and check the key
    section = getattr(config, config_section)
    assert getattr(section, config_key) == expected_value

@pytest.mark.parametrize(
    ('arg_list', 'error_type', 'error_match'),
    [
        # Test invalid device choice
        ([*MIN_ARGS, '--device=tpu'], SystemExit, ''), # ArgumentParser raises SystemExit
        # Test missing participant label when needed
        (['data/', 'out/', 'participant'], SystemExit, ''),
        # Test missing positional args
        (['participant'], SystemExit, ''),
        (['data/', 'participant'], SystemExit, ''),
    ]
)
def test_parser_failures(arg_list, error_type, error_match):
    """Test parser failures for invalid arguments."""
    # Add dummy paths if Path validation is strict
    processed_args = []
    for arg in arg_list:
        if arg == 'data/':
            processed_args.append('/tmp/data') # Use dummy path
        elif arg == 'out/':
            processed_args.append('/tmp/out') # Use dummy path
        else:
            processed_args.append(arg)

    with pytest.raises(error_type, match=error_match):
        parse_args(processed_args)
