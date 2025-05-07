# vi: set ft=python sts=4 ts=4 sw=4 et:
"""Command Line Interface Parser."""

import glob
import logging
import logging.handlers
import re
import sys
from pathlib import Path
from argparse import Action

from packaging.version import Version

from .. import config


# --- Helper Functions & Classes ---


class ToDict(Action):  # Change base class to Action
    """Store argument specs as a dictionary."""

    def __call__(self, parser, namespace, values, option_string=None):
        from argparse import ArgumentError  # Import ArgumentError if needed

        d = {}
        for spec in values:
            try:
                name, loc = spec.split('=')
                loc = Path(loc)
            except ValueError:
                # If only path is provided, use the last part as the name
                loc = Path(spec)
                name = loc.name

            if not name:
                raise ArgumentError(
                    argument=self, message=f'Invalid name derived from spec: {spec}'
                )

            if name in d:
                raise ArgumentError(
                    argument=self, message=f'Received duplicate derivative name: {name}'
                )

            d[name] = loc  # Store the Path object
        setattr(namespace, self.dest, d)


def _path_exists(path, parser):
    """Ensure a given path exists."""
    if path is None or not Path(path).exists():
        raise parser.error(f'Path does not exist: <{path}>')
    return Path(path).resolve()  # Use resolve() for absolute, symlink-resolved path


def _is_file(path, parser):
    """Ensure a given path exists and it is a file."""
    path = _path_exists(path, parser)
    if not path.is_file():
        raise parser.error(f'Path should point to a file (or symlink of file): <{path}>.')
    return path


def _min_one(value, parser):
    """Ensure an argument is not lower than 1."""
    try:
        value = int(value)
        if value < 1:
            raise parser.error("Argument can't be less than one.")
    except (ValueError, TypeError):
        raise parser.error(f'Argument must be an integer >= 1, got: {value}')
    return value


def _to_gb(value):
    """Convert memory size string to gigabytes."""
    scale = {'G': 1, 'T': 10**3, 'M': 1e-3, 'K': 1e-6, 'B': 1e-9}
    value_str = str(value).strip().upper()
    digits = ''.join([c for c in value_str if c.isdigit() or c == '.']) # Allow decimals
    units = value_str[len(digits) :] or 'G' # Default to Gigabytes if no unit

    if not digits:
        raise ValueError(f'Could not extract numeric value from memory string: {value}')

    try:
        digits_float = float(digits)
    except ValueError:
         raise ValueError(f'Invalid numeric value for memory size: {digits}')

    unit_char = units[0] if units else 'G'
    if unit_char not in scale:
        raise ValueError(f'Invalid memory unit: {unit_char}. Use one of {list(scale.keys())}')

    return digits_float * scale[unit_char]


def _drop_sub(value):
    """Remove 'sub-' prefix if present."""
    return value[4:] if isinstance(value, str) and value.startswith('sub-') else value


def _drop_ses(value):
    """Remove 'ses-' prefix if present."""
    return value[4:] if isinstance(value, str) and value.startswith('ses-') else value


def _process_value(value):
    """Process special BIDS query values (*, None)."""
    import bids

    if value is None or value == 'NONE': # Handle string 'NONE' too
        return bids.layout.Query.NONE
    elif value == '*':
        return bids.layout.Query.ANY
    else:
        return value


def _filter_pybids_none_any(dct):
    """Apply _process_value to BIDS filter dictionary."""
    d = {}
    for k, v in dct.items():
        if isinstance(v, list):
            d[k] = [_process_value(val) for val in v]
        else:
            d[k] = _process_value(v)
    return d


def _bids_filter(value, parser):
    """Load BIDS filter file or parse JSON string."""
    from json import JSONDecodeError, loads

    if not value:
        return None

    if isinstance(value, Path) or (isinstance(value, str) and Path(value).exists()):
        path = Path(value)
        if not path.exists():
            raise parser.error(f'BIDS filter file does not exist: <{path}>.')
        try:
            return loads(path.read_text(), object_hook=_filter_pybids_none_any)
        except JSONDecodeError as e:
            raise parser.error(f'JSON syntax error in BIDS filter file: <{path}>. Error: {e}') from e
        except Exception as e:
            raise parser.error(f'Could not read BIDS filter file: <{path}>. Error: {e}') from e
    elif isinstance(value, str):
        # Attempt to parse as JSON string directly
        try:
            return loads(value, object_hook=_filter_pybids_none_any)
        except JSONDecodeError:
            raise parser.error(f'Argument is not a valid path or JSON string: <{value}>.')
    else:
        raise parser.error(f'Invalid BIDS filter value: <{value}>.')


# --- Argument Parser Definition ---


def _build_parser():
    """Build command line argument parser for NCDLMUSE."""
    from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
    from functools import partial

    from ncdlmuse.cli.version import check_latest, is_flagged

    verstr = f'NCDLMUSE v{config.environment.version}'
    currentv = Version(config.environment.version)
    is_release = not any((currentv.is_devrelease, currentv.is_prerelease, currentv.is_postrelease))

    parser = ArgumentParser(
        description=f'NCDLMUSE: BIDS-App wrapper for NiChart DLMUSE v{config.environment.version}',
        formatter_class=ArgumentDefaultsHelpFormatter,
        add_help=False, # Add help manually later
    )

    # Helper functions bound to the parser instance for error reporting
    PathExists = partial(_path_exists, parser=parser)
    IsFile = partial(_is_file, parser=parser)
    PositiveInt = partial(_min_one, parser=parser)
    BIDSFilter = partial(_bids_filter, parser=parser)

    # --- Required Arguments ---
    required = parser.add_argument_group('Required Arguments')
    required.add_argument(
        'bids_dir',
        action='store',
        type=PathExists,
        help=(
            'The root folder of a BIDS valid dataset (sub-XXXXX folders should '
            'be found at the top level in this folder).'
        ),
    )
    required.add_argument(
        'output_dir',
        action='store',
        type=Path, # Validation happens later in parse_args
        help='The directory where results should be stored.',
    )
    required.add_argument(
        'analysis_level',
        choices=['participant', 'group'],
        help=(
             'Level of the analysis that will be performed. Multiple participant level analyses '
             'can be run independently (in parallel) using the same output_dir.'
        ),
    )

    # --- Options for filtering BIDS queries ---
    g_bids = parser.add_argument_group('Options for filtering BIDS queries')
    g_bids.add_argument(
        '--participant-label',
        '--participant_label',
        action='store',
        nargs='+',
        type=_drop_sub,
        help=(
            'A space-delimited list of participant identifiers or a single identifier '
            '(the sub- prefix can be removed). Processing all participants found in the '
            'dataset if not specified.'
        ),
    )
    g_bids.add_argument(
        '-s',
        '--session-id',
        '--session_id',
        dest='session_label',
        action='store',
        nargs='+',
        type=_drop_ses,
        help=(
            'Filter input dataset by session ID. A space-delimited list of session '
            'identifiers or a single identifier (the ses- prefix can be removed). '
            'Processing all sessions found for the selected participants if not specified.'
        ),
    )
    # Add other BIDS filters if needed (e.g., task, run)
    # g_bids.add_argument(
    #     '-t', '--task-id', action='store', nargs='+', help='Filter by task ID'
    # )
    # g_bids.add_argument(
    #     '-r', '--run-id', action='store', nargs='+', type=int, help='Filter by run ID'
    # )
    g_bids.add_argument(
        '--bids-filter-file',
        dest='bids_filters',
        action='store',
        type=BIDSFilter, # Uses the combined file/string parser
        metavar='PATH|JSON',
        help=(
            'Path to a JSON file OR a JSON string defining PyBIDS filters '
            '(sessions, tasks, runs, modalities, etc.). Overrides other filtering arguments '
            'if specified. See documentation for format.'
        ),
    )
    g_bids.add_argument(
        '--skip-bids-validation',
        '--skip_bids_validation',
        action='store_true',
        default=False,
        help='Assume the input dataset is BIDS compliant and skip the validation.',
    )
    g_bids.add_argument(
        '--bids-database-dir',
        metavar='PATH',
        type=Path,
        help=(
            'Path to an existing PyBIDS database folder OR a folder where the database '
            'should be created, for faster BIDS indexing (especially useful for large datasets).'
        ),
    )
    g_bids.add_argument(
        '-d',
        '--derivatives',
        action=ToDict, # Use the custom action
        metavar='NAME=PATH',
        nargs='+',
        help=(
            'Specify one or more paths to pre-computed derivative datasets. '
            'Each path should be specified as NAME=PATH. The NAME is a identifier '
            'for the derivatives (e.g., `smriprep=/path/to/smriprep`). '
            'NCDLMUSE will search these directories for relevant inputs.'
        ),
    )

    # --- NiChart_DLMUSE Specific Options ---
    g_dlmuse = parser.add_argument_group('Specific options for NiChart_DLMUSE processing')
    g_dlmuse.add_argument(
        '--device',
        action='store',
        type=str,
        choices=['cpu', 'cuda', 'mps'],
        default='cpu',
        help='Device for DLMUSE model inference (cpu, cuda, mps). Defaults to cpu.',
    )
    g_dlmuse.add_argument(
        '--model-folder',
        metavar='PATH',
        type=PathExists,
        help='Path to the folder containing custom nnU-Net models (DLICV and DLMUSE).',
    )
    g_dlmuse.add_argument(
        '--derived-roi-map',
        dest='dlmuse_derived_roi_mappings_file',
        metavar='FILE',
        type=IsFile,
        help='Path to the CSV file mapping MUSE ROIs to derived ROIs.',
    )
    g_dlmuse.add_argument(
        '--muse-roi-map',
        dest='dlmuse_muse_roi_mappings_file',
        metavar='FILE',
        type=IsFile,
        help='Path to the CSV file mapping MUSE ROIs to consecutive indices.',
    )
    g_dlmuse.add_argument(
        '--all-in-gpu',
        action='store_true',
        default=False,
        help='Attempt to load and run the entire model on the GPU (if available and applicable).',
    )
    g_dlmuse.add_argument(
        '--disable-tta',
        action='store_true',
        default=False,
        help='Disable Test-Time Augmentation during inference.',
    )
    g_dlmuse.add_argument(
        '--clear-cache',
        action='store_true',
        default=False,
        help='Clear the NiChart_DLMUSE model download cache before running.',
    )

    # --- Performance Options ---
    g_perfm = parser.add_argument_group('Options to handle performance')
    g_perfm.add_argument(
        '--nprocs',
        '--nthreads',
        '--n_cpus',
        '--n-cpus',
        dest='n_procs',
        action='store',
        type=PositiveInt,
        help='Maximum number of CPUs across all processes.',
    )
    g_perfm.add_argument(
        '--omp-nthreads',
        action='store',
        type=PositiveInt,
        help='Maximum number of threads per-process (OpenMP).',
    )
    g_perfm.add_argument(
        '--mem',
        '--mem_gb',
        '--mem-gb',
        dest='mem_gb',
        action='store',
        type=_to_gb, # Use GB converter
        metavar='SIZE',
        help='Upper bound memory limit for NCDLMUSE processes (e.g., 8G).',
    )
    g_perfm.add_argument(
        '--low-mem',
        action='store_true',
        help='Attempt to reduce memory usage (will increase disk usage in working directory).',
    )
    g_perfm.add_argument(
        '--use-plugin',
        '--nipype-plugin-file',
        action='store',
        metavar='FILE',
        type=IsFile,
        help='Nipype plugin configuration file.',
    )
    g_perfm.add_argument(
        '-w',
        '--work-dir',
        action='store',
        type=Path,
        help=(
            'Path where intermediate results should be stored. Defaults to '
            '<output_dir>/ncdlmuse_wf/'
        )
    )
    g_perfm.add_argument(
        '--resource-monitor',
        action='store_true',
        default=False,
        help="Enable Nipype's resource monitoring.",
    )
    g_perfm.add_argument(
        '--stop-on-first-crash',
        action='store_true',
        default=False,
        help='Force stopping on first crash, even if a work directory was specified.',
    )
    g_perfm.add_argument(
        '--sloppy',
        action='store_true',
        default=False,
        help='Run in sloppy mode (lower quality checks, faster processing) - TESTING ONLY',
    )

    # --- Workflow Subset Options ---
    g_subset = parser.add_argument_group('Options for performing only a subset of the workflow')
    g_subset.add_argument(
        '--boilerplate-only', '--boilerplate_only',
        action='store_true', default=False,
        help='Generate boilerplate script and exit.'
    )
    g_subset.add_argument(
        '--reports-only', action='store_true', default=False,
        help=(
            "Only generate reports, don't run workflows. Assumes workflows finished successfully."
        )
    )

    # --- Output Modulation Options ---
    g_outputs = parser.add_argument_group('Options for modulating outputs')
    g_outputs.add_argument(
        '--aggregate-session-reports',
        dest='aggr_ses_reports',
        action='store',
        type=PositiveInt,
        default=4, # Match aslprep default, adjust if needed
        help=(
            "Maximum number of sessions aggregated in one subject's visual report. "
            'If exceeded, visual reports are split by session. Default: %(default)s.'
        ),
    )
    g_outputs.add_argument(
        '--md-only-boilerplate', action='store_true', default=False,
        help='Skip generation of HTML and LaTeX formatted citation boilerplate.'
    )
    g_outputs.add_argument(
        '--write-graph', action='store_true', default=False,
        help='Write workflow graph (.dot/.svg).'
    )

    # --- Other Options ---
    g_other = parser.add_argument_group('Other options')
    g_other.add_argument(
        '--config-file', action='store', metavar='FILE', type=IsFile,
        help='Use pre-generated configuration file.',
    )
    g_other.add_argument(
        '-h', '--help', action='help', help='Show this help message and exit.'
    )
    g_other.add_argument('--version', action='version', version=verstr)
    g_other.add_argument(
        '-v', '--verbose', dest='verbose_count', action='count', default=0,
        help='Increases log verbosity for each occurrence (up to -vvv for DEBUG).',
    )
    g_other.add_argument(
        '--notrack', action='store_true', default=False,
        help='Opt-out of sending Sentry usage tracking information.',
    )
    g_other.add_argument(
        '--debug', action='store', nargs='+', choices=config.DEBUG_MODES + ('all',),
        help="Enable debug mode(s). Use 'all' for all modes.",
    )
    g_other.add_argument(
        '--random-seed', dest='_random_seed', action='store', type=int, default=None,
        help='Initialize the random seed for the workflow for reproducibility.',
    )

    # --- Check for Latest Version and Flags ---
    try:
        latest = check_latest()
        if latest is not None and currentv < latest:
            print(
                f'WARNING: You are using ncdlmuse-{currentv}, '
                f'and a newer version ({latest}) is available.\n'
                'Please check documentation for upgrade instructions.',
                file=sys.stderr,
            )
    except Exception as e:
         print(f'WARNING: Could not check for latest version: {e}', file=sys.stderr)

    try:
        is_flagged_res = is_flagged()
        if is_flagged_res[0]:
            _reason = is_flagged_res[1] or 'unknown'
            print(
                f'WARNING: Version {config.environment.version} of NCDLMUSE has been FLAGGED\n'
                f'(reason: {_reason}).\n'
                'Severe flaws may be present. Usage is strongly discouraged.',
                file=sys.stderr,
            )
    except Exception as e:
        print(
            f'WARNING: Could not check if version '
            f'{config.environment.version} is flagged: {e}',
            file=sys.stderr
        )

    return parser


# --- Argument Parsing Logic ---


def parse_args(args=None, namespace=None):
    """Parse command line arguments and store settings in config module."""
    from nipype import config as nipype_config
    from niworkflows.utils.misc import clean_directory # Use niworkflows cleaner if needed

    parser = _build_parser()
    opts = parser.parse_args(args, namespace)

    # --- Load Configuration ---
    # 1. Load from file if specified (--config-file)
    if opts.config_file:
        try:
            # Skip run_uuid when loading from file to avoid reusing old ones
            skip = {'execution': ('run_uuid',)} if not opts.reports_only else {}
            config.load(opts.config_file, skip=skip, init=False)
            config.loggers.cli.info(f'Loaded previous configuration file {opts.config_file}')
        except Exception as e:
            print(f'ERROR: Could not load config file "{opts.config_file}": {e}', file=sys.stderr)

    # 2. Set up run_uuid (either new or loaded) BEFORE applying CLI args
    # This ensures CLI args related to directories use the correct UUID
    config.execution.run_uuid # Accessing it initializes if not already set

    # 3. Apply CLI arguments (overriding file config and defaults)
    cli_vars = vars(opts)
    config.from_dict(cli_vars, init=False) # Use config's internal update mechanism
    config.execution.cmdline = sys.argv[:] # Store the full command line
    
    # --- Explicitly map DLMUSE options ---
    # Ensure CLI args for DLMUSE are correctly mapped to workflow.dlmuse_* config
    # This overrides values loaded from file or defaults if CLI arg was provided
    if cli_vars.get('device') is not None:
        config.workflow.dlmuse_device = cli_vars['device']
    if cli_vars.get('model_folder') is not None:
        config.workflow.dlmuse_model_folder = cli_vars['model_folder']
    if cli_vars.get('dlmuse_derived_roi_mappings_file') is not None:
        config.workflow.dlmuse_derived_roi_mappings_file = cli_vars['dlmuse_derived_roi_mappings_file']
    if cli_vars.get('dlmuse_muse_roi_mappings_file') is not None:
        config.workflow.dlmuse_muse_roi_mappings_file = cli_vars['dlmuse_muse_roi_mappings_file']
    # Booleans are always present in cli_vars (True/False), so update directly
    config.workflow.dlmuse_all_in_gpu = cli_vars['all_in_gpu']
    config.workflow.dlmuse_disable_tta = cli_vars['disable_tta']
    config.workflow.dlmuse_clear_cache = cli_vars['clear_cache']

    # --- Resolve and Finalize Paths ---
    # BIDS Dir (required, checked by PathExists in parser)
    config.execution.bids_dir = config.execution.bids_dir.resolve()

    # Output Dir (resolve and create)
    config.execution.output_dir = config.execution.output_dir.resolve()
    config.execution.output_dir.mkdir(exist_ok=True, parents=True)

    # NCDLMUSE Dir (derivatives base, ensure it's within output)
    config.execution.ncdlmuse_dir = config.execution.output_dir
    config.execution.ncdlmuse_dir.mkdir(exist_ok=True, parents=True)

    # Work Dir (resolve, create, handle default)
    if config.execution.work_dir:
        config.execution.work_dir = config.execution.work_dir.resolve()
    else:
        # Default work_dir inside output_dir
        config.execution.work_dir = config.execution.output_dir / 'ncdlmuse_wf'
    config.execution.work_dir.mkdir(exist_ok=True, parents=True)
    config.loggers.cli.info(f'Using working directory: {config.execution.work_dir}')

    # Log Dir (determine path based on analysis level, create)
    # Default log directory (used for group level or multi-subject runs)
    log_dir_base = config.execution.ncdlmuse_dir / 'logs'
    run_uuid = config.execution.run_uuid

    # Check for single-subject participant-level analysis for specific log structure
    subj_specific_log = False
    if (
        config.execution.analysis_level == 'participant' and
        config.execution.participant_label and
        len(config.execution.participant_label) == 1
    ):
        subj_label = config.execution.participant_label[0]
        # Use structure: <output_dir>/ncdlmuse/sub-<label>/logs/<uuid>/
        config.execution.log_dir = (
            config.execution.ncdlmuse_dir / 
            f'sub-{subj_label}' / 
            'logs' / 
            run_uuid
        )
        config.loggers.cli.info(
            f'Running participant level analysis for {subj_label}, '
            f'using log directory: {config.execution.log_dir}'
        )
        subj_specific_log = True
    else:
        # Default location for group or multi-subject participant runs
        config.execution.log_dir = log_dir_base / run_uuid
        config.loggers.cli.info(f'Using default log directory: {config.execution.log_dir}')

    config.execution.log_dir.mkdir(exist_ok=True, parents=True)


    # --- Setup Logging ---
    # Determine log level from verbosity count
    log_level = int(max(25 - 5 * config.execution.verbose_count, logging.DEBUG))
    config.execution.log_level = log_level # Store the determined level

    # Configure root logger and handlers (will use config.execution.log_dir)
    _setup_logging(log_level)
    build_log = config.loggers.cli # Use this logger after setup


    # --- Configure Nipype ---
    # Update nipype config with paths and settings
    nipype_settings = {
        'logging': {
            'log_directory': str(config.execution.log_dir),
            'log_to_file': False, # Disable nipype's pypeline.log file
            'workflow_level': logging.getLevelName(log_level),
            'interface_level': logging.getLevelName(log_level),
            'log_format': '%(asctime)s %(name)s %(levelname)s:\n\t %(message)s',
            'datefmt': '%%y%%m%%d-%%H:%%M:%%S'
        },
        'execution': {
            'crashdump_dir': str(config.execution.log_dir),
            'stop_on_first_crash': config.nipype.stop_on_first_crash,
            'hash_method': 'content',
            'crashfile_format': 'txt',
            'remove_unnecessary_outputs': False,
            'remove_node_directories': False,
            'check_version': False,  # disable future telemetry
            'get_linked_libs': config.nipype.get_linked_libs,
        },
        'monitoring': {
            'enabled': config.nipype.resource_monitor,
            'sample_frequency': '0.5',
            'summary_append': True,
        } if config.nipype.resource_monitor else {},
    }

    nipype_config.update_config(nipype_settings)

    # Control Nipype logging to prevent duplication
    nipype_config.enable_debug_mode()  # This prevents Nipype from adding its own default file/workflow handlers

    # Explicitly clear any pre-existing handlers from Nipype's loggers
    # to ensure only our root logger's handlers (via propagation) are used.
    nipype_logger_names = ['nipype', 'nipype.workflow', 'nipype.interface', 'nipype.utils']
    for logger_name in nipype_logger_names:
        logger_instance = logging.getLogger(logger_name)
        if hasattr(logger_instance, 'handlers'):
            for handler in logger_instance.handlers[:]:  # Iterate over a copy
                logger_instance.removeHandler(handler)
                try:
                    handler.close()  # Attempt to close handler
                except Exception:
                    pass  # Ignore errors during handler closing
    
    # Allow Nipype logs to propagate to our handlers
    logging.getLogger('nipype.workflow').propagate = True


    # --- Resource Management Checks ---
    # Check thread counts
    if (
        config.nipype.omp_nthreads is not None and
        config.nipype.n_procs is not None and
        config.nipype.n_procs > 0 and # Avoid division by zero or illogical checks
        config.nipype.n_procs < config.nipype.omp_nthreads
    ):
        build_log.warning(
            f'Per-process threads (--omp-nthreads={config.nipype.omp_nthreads}) '
            f'exceed total CPUs (--nprocs={config.nipype.n_procs}). This may lead to '
            'inefficient resource usage.'
        )


    # --- Validate BIDS Dataset and Select Subjects/Sessions ---
    if not config.execution.skip_bids_validation or not config.execution.layout:
        from bids.layout import BIDSLayout, BIDSLayoutIndexer
        from bids.config import set_options as bids_set_options

        build_log.info(f'Found BIDS dataset at: {config.execution.bids_dir}')
        # Check for dataset_description.json before creating layout
        dataset_desc_path = config.execution.bids_dir / 'dataset_description.json'
        if not dataset_desc_path.is_file():
            build_log.warning(
                f'dataset_description.json not found at BIDS root: {dataset_desc_path}. '
                f'PyBIDS indexing may fail or be incorrect, even with validation skipped.'
            )
            # Depending on desired strictness, could raise parser.error here

        bids_validate = not config.execution.skip_bids_validation
        build_log.info(
            f'Running PyBIDS indexing (validation {"enabled" if bids_validate else "disabled"})...'
        )

        # Determine database path (might be None)
        database_path = config.execution.bids_database_dir
        if database_path:
            database_path = database_path.resolve()
            database_path.mkdir(exist_ok=True, parents=True)
        else:
            # Instruct BIDSLayout to use a default path if database_path is None
            # by not providing database_path=None, but letting it default
            pass

        # Define ignore patterns
        ignore_patterns = (
            'code', 'stimuli', 'sourcedata', 'models',
            'derivatives', re.compile(r'^\.') # Correct regex for hidden files
        )

        try:
            # Create BIDSLayoutIndexer with validation and ignore settings
            bids_indexer = BIDSLayoutIndexer(
                validate=bids_validate,
                ignore=ignore_patterns,
            )
            layout = BIDSLayout(
                root=str(config.execution.bids_dir),
                # Set database_path=None and reset_database=True for in-memory/fresh index
                database_path=None,
                indexer=bids_indexer, # Pass the configured indexer
                reset_database=True, # Force fresh index
            )
            config.execution.layout = layout # Store layout in config

        except Exception as e:
            build_log.critical(f'PyBIDS failed to index BIDS dataset: {e}')
            build_log.critical(
                'Please check the dataset structure and PyBIDS installation. '
                'If you believe the dataset is valid, use --skip-bids-validation.'
            )
            sys.exit(1)

        # --- Filter Subjects/Sessions ---
        all_subjects = layout.get_subjects()
        if not all_subjects:
            build_log.critical('No subjects found in BIDS dataset. Check filters and dataset.')
            sys.exit(1)

        # Select subjects
        if config.execution.participant_label:
            selected_subjects = set(config.execution.participant_label)
            missing_subjects = selected_subjects - set(all_subjects)
            if missing_subjects:
                parser.error(
                    'One or more participant labels were not found in the BIDS directory: '
                    f'{", ".join(sorted(list(missing_subjects)))}.'
                )
            config.execution.participant_label = sorted(list(selected_subjects))
            build_log.info(
                f"Processing specified participants: "
                f"{', '.join(config.execution.participant_label)}"
            )
        else:
            config.execution.participant_label = sorted(all_subjects)
            build_log.info(
                f"Processing all {len(all_subjects)} participants found in BIDS dataset."
            )

        # Validate / Select sessions (if --session-id was used)
        if config.execution.session_label:
            selected_sessions = set(config.execution.session_label)
            found_sessions_for_participants = set()
            for subj in config.execution.participant_label:
                found_sessions_for_participants.update(
                     layout.get_sessions(subject=subj) or []
                )

            missing_sessions = selected_sessions - found_sessions_for_participants
            if missing_sessions:
                 build_log.warning(
                      f'Specified session labels not found for *all* selected participants: '
                      f'{", ".join(sorted(list(missing_sessions)))}. '
                      'Ensure these sessions exist for the intended participants.'
                 )

            config.execution.session_label = sorted(list(selected_sessions))
            build_log.info(
                 f"Filtering input data by specified sessions: "
                 f"{', '.join(config.execution.session_label)}"
            )
        else:
            build_log.info("Processing all sessions found for the selected participants.")

    # --- Collect T1w file list --- #
    if config.execution.layout:
        from bids.layout import Query # Import Query if needed
        try:
            config.execution.t1w_list = config.execution.layout.get(
                suffix='T1w',
                extension=['.nii', '.nii.gz'],
                return_type='file',
            )

            if not config.execution.t1w_list:
                err_msg = (
                    f'No T1w files found for participants: {config.execution.participant_label} '
                    f'and sessions: {config.execution.session_label}. '
                    f'Check BIDS dataset and filters.'
                )
                build_log.critical(err_msg)
                parser.error(err_msg) # Exit cleanly via parser error
            else:
                build_log.info(f'Found {len(config.execution.t1w_list)} T1w files for processing.')
        except Exception as e:
            build_log.critical(f'Error querying BIDS layout for T1w files: {e}')
            sys.exit(1)
    else:
        # Handle case where layout couldn't be created earlier (e.g., skip-validation failed)
        build_log.critical('BIDS layout not available, cannot collect T1w files.')
        sys.exit(1)

    # --- Final Path Checks ---
    # Ensure output_dir is not inside bids_dir
    if config.execution.output_dir == config.execution.bids_dir:
        rec_path = config.execution.bids_dir / 'derivatives' / 'ncdlmuse'
        parser.error(
            'The selected output folder is the same as the input BIDS folder. '
            f'Please modify the output path (suggestion: {rec_path}).'
        )
    # Ensure work_dir is not inside bids_dir 
    # (unless bids_dir is explicitly '.', which is unlikely for BIDS root)
    if (config.execution.bids_dir != Path('.') and 
            config.execution.bids_dir in config.execution.work_dir.parents):
        parser.error(
            'The selected working directory is a subdirectory of the input BIDS dataset. '
            'This modifies the input dataset, which is forbidden and can lead to unexpected results. '
            f'Please modify the output path (suggestion: {rec_path}).'
        )

    # --- Save Final Configuration ---
    try:
        # Use the specific log directory determined earlier
        config_file = config.execution.log_dir / 'ncdlmuse.toml'
        config.to_filename(config_file)
        build_log.info(f'Final configuration saved to: {config_file}')
    except Exception as e:
        build_log.error(f'Failed to save final configuration to {config_file}: {e}')


    # --- Update the config object one last time to ensure all sections are initialized ---
    # This ensures that sections not explicitly touched (like 'workflow') are generated
    # with their defaults if they haven't been loaded or set.
    config.from_dict({})


# --- Logging Setup ---


def _setup_logging(level):
    """Configure logging facilities for NCDLMUSE.

    Sets up logging to standard output and derivatives log directory.
    Allows Nipype log messages to propagate to the handlers defined here.
    """
    import sys
    from nipype import config as ncfg # Use nipype config directly for log settings

    # Use log directory determined in parse_args
    log_dir = config.execution.log_dir
    log_file = log_dir / f'ncdlmuse_{config.execution.run_uuid}.log'

    # Define log format
    log_format = logging.Formatter(
        '%(asctime)s %(name)s %(levelname)s:\n\t %(message)s',
        datefmt='%y%m%d-%H:%M:%S',
    )

    # --- Root Logger Configuration ---
    root_logger = logging.getLogger()

    # Remove existing handlers to avoid duplicates if re-configuring
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass # Ignore errors during handler closing

    # Set root logger level *before* adding handlers
    root_logger.setLevel(level)

    # --- Console Handler ---
    # Only add console handler if level is INFO or lower (more verbose)
    # Or adjust this logic based on desired console output behavior
    if level <= logging.INFO: # Example: Show INFO and DEBUG on console
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(log_format)
        console_handler.setLevel(level) # Handler level respects root level
        root_logger.addHandler(console_handler)

    # --- File Handler ---
    try:
        # Use RotatingFileHandler from aslprep example
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=5 # 5MB limit, 5 backups
        )
        file_handler.setFormatter(log_format)
        file_handler.setLevel(level) # Log everything >= level to file
        root_logger.addHandler(file_handler)
    except Exception as e:
        # Fallback or warning if file logging fails
        logging.getLogger('cli').error(f'Could not open log file {log_file}: {e}')

    # --- Configure Nipype Logging ---
    # Set Nipype's config to use our log directory and file logging
    ncfg.update_config(
        {
            'logging': {
                'log_directory': str(log_dir),
                'log_to_file': False, # Disable nipype's pypeline.log file
            }
        }
    )
    # Set Nipype's internal logging levels based on our overall level
    nipype_level_str = logging.getLevelName(level)
    ncfg.set('logging', 'workflow_level', nipype_level_str)
    ncfg.set('logging', 'interface_level', nipype_level_str)

    # --- Control Propagation ---
    # Allow nipype messages to propagate to root logger's handlers
    logging.getLogger('nipype.workflow').propagate = True
    logging.getLogger('nipype.interface').propagate = True
    logging.getLogger('nipype.utils').propagate = True

    # Prevent double logging from our own CLI logger if it's separate
    logging.getLogger('cli').propagate = False # Already handled by root

    logging.getLogger('cli').info(f'Logging configured (Level: {logging.getLevelName(level)})')
    logging.getLogger('cli').info(f'Log file: {log_file}')