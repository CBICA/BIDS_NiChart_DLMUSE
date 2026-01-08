# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
#
# Copyright 2023 The NiPreps Developers <nipreps@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# We support and encourage derived works from this project, please read
# about our expectations at
#
#     https://www.nipreps.org/community/licensing/
#
r"""
A Python module to maintain unique, run-wide *ncdlmuse* settings.

This module implements the memory structures to keep a consistent, singleton config.
Settings are passed across processes via filesystem, and a copy of the settings for
each run and subject is left under
``<ncdlmuse_dir>/sub-<participant_id>/log/<run_unique_id>/ncdlmuse.toml``.
Settings are stored using :abbr:`ToML (Tom's Markup Language)`.
The module has a :py:func:`~ncdlmuse.config.to_filename` function to allow writing out
the settings to hard disk in *ToML* format, which looks like:

.. literalinclude:: ../ncdlmuse/data/tests/config.toml
   :language: toml
   :name: ncdlmuse.toml
   :caption: **Example file representation of ncdlmuse settings**.

This config file is used to pass the settings across processes,
using the :py:func:`~ncdlmuse.config.load` function.

Configuration sections
----------------------
.. autoclass:: environment
   :members:
.. autoclass:: execution
   :members:
.. autoclass:: workflow
   :members:
.. autoclass:: nipype
   :members:

Usage
-----
A config file is used to pass settings and collect information as the execution
graph is built across processes.

.. code-block:: Python

    from ncdlmuse import config
    config_file = config.execution.work_dir / '.ncdlmuse.toml'
    config.to_filename(config_file)
    # Call build_workflow(config_file, retval) in a subprocess
    with Manager() as mgr:
        from ncdlmuse.cli.workflow import build_workflow
        retval = mgr.dict()
        p = Process(target=build_workflow, args=(str(config_file), retval))
        p.start()
        p.join()
    config.load(config_file)
    # Access configs from any code section as:
    value = config.section.setting

Logging
-------
.. autoclass:: loggers
   :members:

Other responsibilities
----------------------
The :py:mod:`config` is responsible for other conveniency actions.

  * Switching Python's :obj:`multiprocessing` to *forkserver* mode.
  * Set up a filter for warnings as early as possible.
  * Automated I/O magic operations. Some conversions need to happen in the
    store/load processes (e.g., from/to :obj:`~pathlib.Path` \<-\> :obj:`str`,
    :py:class:`~bids.layout.BIDSLayout`, etc.)

"""

import os
from multiprocessing import set_start_method

# Disable NiPype etelemetry always
_disable_et = bool(os.getenv('NO_ET') is not None or os.getenv('NIPYPE_NO_ET') is not None)
os.environ['NIPYPE_NO_ET'] = '1'
os.environ['NO_ET'] = '1'

CONFIG_FILENAME = 'ncdlmuse.toml'

try:
    set_start_method('forkserver')
except RuntimeError:
    pass  # context has been already set
finally:
    # Defer all custom import for after initializing the forkserver and
    # ignoring the most annoying warnings
    import random
    import sys
    from pathlib import Path
    from time import strftime
    from uuid import uuid4

    from nipype import __version__ as _nipype_ver
    from templateflow import __version__ as _tf_ver

    from ncdlmuse import __version__

if not hasattr(sys, '_is_pytest_session'):
    sys._is_pytest_session = False  # Trick to avoid sklearn's FutureWarnings
# Disable all warnings in main and children processes only on production versions
if not any(
    (
        '+' in __version__,
        __version__.endswith('.dirty'),
        os.getenv('NCDLMUSE_DEV', '0').lower() in ('1', 'on', 'true', 'y', 'yes'),
    )
):
    from ncdlmuse._warnings import logging

    os.environ['PYTHONWARNINGS'] = 'ignore'
elif os.getenv('NCDLMUSE_WARNINGS', '0').lower() in ('1', 'on', 'true', 'y', 'yes'):
    # allow disabling warnings on development versions
    # https://github.com/nipreps/fmriprep/pull/2080#discussion_r409118765
    from ncdlmuse._warnings import logging
else:
    import logging

logging.addLevelName(25, 'IMPORTANT')  # Add a new level between INFO and WARNING
logging.addLevelName(15, 'VERBOSE')  # Add a new level between INFO and DEBUG

DEFAULT_MEMORY_MIN_GB = 0.01

# Ping NiPype eTelemetry once if env var was not set
# workers on the pool will have the env variable set from the master process
if not _disable_et:
    # Just get so analytics track one hit
    from contextlib import suppress

    from requests import ConnectionError, ReadTimeout  # noqa: A004
    from requests import get as _get_url

    with suppress((ConnectionError, ReadTimeout)):
        _get_url('https://rig.mit.edu/et/projects/nipy/nipype', timeout=0.05)

# Execution environment
_exec_env = os.name
_docker_ver = None
# special variable set in the container
if os.getenv('IS_DOCKER_8395080871'):
    _exec_env = 'singularity'
    _cgroup = Path('/proc/1/cgroup')
    if _cgroup.exists() and 'docker' in _cgroup.read_text():
        _docker_ver = os.getenv('DOCKER_VERSION_8395080871')
        _exec_env = 'ncdlmuse-docker' if _docker_ver else 'docker'
    del _cgroup

_templateflow_home = Path(
    os.getenv('TEMPLATEFLOW_HOME', os.path.join(os.getenv('HOME'), '.cache', 'templateflow'))
)

try:
    from psutil import virtual_memory

    _free_mem_at_start = round(virtual_memory().available / 1024**3, 1)
except Exception:  # noqa: BLE001
    _free_mem_at_start = None

_oc_limit = 'n/a'
_oc_policy = 'n/a'
try:
    # Memory policy may have a large effect on types of errors experienced
    _proc_oc_path = Path('/proc/sys/vm/overcommit_memory')
    if _proc_oc_path.exists():
        _oc_policy = {'0': 'heuristic', '1': 'always', '2': 'never'}.get(
            _proc_oc_path.read_text().strip(), 'unknown'
        )
        if _oc_policy != 'never':
            _proc_oc_kbytes = Path('/proc/sys/vm/overcommit_kbytes')
            if _proc_oc_kbytes.exists():
                _oc_limit = _proc_oc_kbytes.read_text().strip()
            if _oc_limit in ('0', 'n/a') and Path('/proc/sys/vm/overcommit_ratio').exists():
                _oc_limit = f'{Path("/proc/sys/vm/overcommit_ratio").read_text().strip()}%'
except Exception:  # noqa: S110, BLE001
    pass


# Debug modes are names that influence the exposure of internal details to
# the user, either through additional derivatives or increased verbosity
DEBUG_MODES = ('pdb',)


class _Config:
    """An abstract class forbidding instantiation.

    Provides basic dictionary-based configuration loading and retrieval.
    """

    _paths = ()

    def __init__(self):
        """Avert instantiation."""
        raise RuntimeError('Configuration type is not instantiable.')

    @classmethod
    def load(cls, settings, init=True, ignore=None):
        """Store settings from a dictionary."""
        ignore = ignore or {}
        for k, v in settings.items():
            if k in ignore or v is None:
                continue
            if k in cls._paths:
                if isinstance(v, list | tuple):
                    setattr(cls, k, [Path(val).absolute() for val in v])
                elif isinstance(v, dict):
                    setattr(cls, k, {key: Path(val).absolute() for key, val in v.items()})
                else:
                    setattr(cls, k, Path(v).absolute())
            elif hasattr(cls, k):
                setattr(cls, k, v)

        if init:
            try:
                cls.init()
            except AttributeError:
                pass

    @classmethod
    def get(cls):
        """Return defined settings."""
        from niworkflows.utils.spaces import Reference, SpatialReferences

        out = {}
        for k, v in cls.__dict__.items():
            if k.startswith('_') or v is None:
                continue
            if callable(getattr(cls, k)):
                continue
            if k in cls._paths:
                if isinstance(v, list | tuple):
                    v = [str(val) for val in v]
                elif isinstance(v, dict):
                    v = {key: str(val) for key, val in v.items()}
                else:
                    v = str(v)
            if isinstance(v, SpatialReferences):
                v = ' '.join(str(s) for s in v.references) or None
            if isinstance(v, Reference):
                v = str(v) or None
            out[k] = v
        return out


class environment(_Config):
    """
    Read-only options regarding the platform and environment.

    Crawls runtime descriptive settings (e.g., execution environment,
    nipype and *NCDLMUSE* versions, etc.).
    The ``environment`` section is not loaded in from file,
    only written out when settings are exported.
    This config section is useful when reporting issues,
    and these variables are tracked whenever the user does not
    opt-out using the ``--notrack`` argument.

    """

    cpu_count = os.cpu_count()
    """Number of available CPUs."""
    exec_docker_version = _docker_ver
    """Version of Docker Engine."""
    exec_env = _exec_env
    """A string representing the execution platform."""
    free_mem = _free_mem_at_start
    """Free memory at start."""
    overcommit_policy = _oc_policy
    """Linux's kernel virtual memory overcommit policy."""
    overcommit_limit = _oc_limit
    """Linux's kernel virtual memory overcommit limits."""
    nipype_version = _nipype_ver
    """Nipype's current version."""
    templateflow_version = _tf_ver
    """The TemplateFlow client version installed."""
    version = __version__
    """*NCDLMUSE*'s version."""


class nipype(_Config):
    """Nipype settings."""

    crashfile_format = 'txt'
    """The file format for crashfiles, either text or pickle."""
    get_linked_libs = False
    """Run NiPype's tool to enlist linked libraries for every interface."""
    mem_gb = None
    """Estimation in GB of the RAM this workflow can allocate at any given time."""
    n_procs = os.cpu_count()
    """Number of processes (compute tasks) that can be run in parallel (multiprocessing only)."""
    omp_nthreads = None
    """Number of CPUs a single process can access for multithreaded execution."""
    plugin = 'MultiProc'
    """NiPype's execution plugin."""
    plugin_args = {
        'maxtasksperchild': 1,
        'raise_insufficient': False,
    }
    """Settings for NiPype's execution plugin."""
    plugin_file = None
    """Path to Nipype plugin configuration file."""
    resource_monitor = True
    """Enable resource monitor."""
    stop_on_first_crash = True
    """Whether the workflow should stop or continue after the first error."""

    @classmethod
    def get_plugin(cls):
        """Format a dictionary for Nipype consumption."""
        out = {
            'plugin': cls.plugin,
            'plugin_args': cls.plugin_args,
        }
        if cls.plugin in ('MultiProc', 'LegacyMultiProc'):
            out['plugin_args']['n_procs'] = int(cls.n_procs)
            if cls.mem_gb:
                out['plugin_args']['memory_gb'] = float(cls.mem_gb)
        return out

    @classmethod
    def init(cls):
        """Set NiPype configurations."""
        from nipype import config as ncfg

        # Configure resource_monitor
        if cls.resource_monitor:
            ncfg.update_config(
                {
                    'monitoring': {
                        'enabled': cls.resource_monitor,
                        'sample_frequency': '0.5',
                        'summary_append': True,
                    }
                }
            )
            ncfg.enable_resource_monitor()

        # Nipype config (logs and execution)
        ncfg.update_config(
            {
                'execution': {
                    'crashdump_dir': str(execution.log_dir),
                    'crashfile_format': cls.crashfile_format,
                    'get_linked_libs': cls.get_linked_libs,
                    'stop_on_first_crash': cls.stop_on_first_crash,
                    'check_version': False,  # disable future telemetry
                }
            }
        )

        if cls.omp_nthreads is None:
            cls.omp_nthreads = min(cls.n_procs - 1 if cls.n_procs > 1 else os.cpu_count(), 8)


class execution(_Config):
    """Configure run-level settings."""

    analysis_level = 'participant'
    """Level of the analysis that will be performed ('participant' or 'group')."""
    add_provenance = False
    """Include provenance information in group-level aggregation output."""
    bids_dir = None
    """An existing path to the dataset, which must be BIDS-compliant."""
    bids_database_dir = None
    """Path to the directory containing SQLite database indices for the input BIDS dataset."""
    bids_description_hash = None
    """Checksum (SHA256) of the ``dataset_description.json`` of the BIDS dataset."""
    bids_filters = None
    """A dictionary of BIDS selection filters."""
    boilerplate_only = False
    """Only generate a boilerplate."""
    cmdline = []
    """Command line arguments used to invoke ncdlmuse."""
    debug = []
    """Debug mode(s)."""
    ncdlmuse_dir = None
    """Root of NCDLMUSE BIDS Derivatives dataset."""
    layout = None
    """A :py:class:`~bids.layout.BIDSLayout` object, see :py:func:`init`."""
    log_dir = None
    """The path to a directory that contains execution logs."""
    log_level = 25
    """Output verbosity."""
    low_mem = False
    """Utilize uncompressed NIfTIs and other tricks to minimize memory allocation."""
    md_only_boilerplate = False
    """Do not convert boilerplate from MarkDown to LaTex and HTML."""
    notrack = False
    """Do not collect telemetry information for *NCDLMUSE*."""
    output_dir = None
    """Folder where derivatives will be stored."""
    output_spaces = None
    """Standard and non-standard spaces to resample anatomical and functional images to."""
    reports_only = False
    """Only build the reports, based on the reportlets found in a cached working directory."""
    run_uuid = f'{strftime("%Y%m%d-%H%M%S")}_{uuid4()}'
    """Unique identifier of this particular run."""
    skip_bids_validation = False
    """Skip BIDS validation."""
    participant_label = None
    """List of participant identifiers that are to be preprocessed."""
    session_label = None
    """List of session identifiers that are to be preprocessed."""
    templateflow_home = _templateflow_home
    """The root folder of the TemplateFlow client."""
    verbose_count = 0
    """Verbosity level counter set by CLI -v flags."""
    work_dir = None
    """Path to a working directory where intermediate results will be available."""
    write_graph = False
    """Write out the computational graph corresponding to the planned preprocessing."""
    hires = False
    """Request higher resolution outputs produced using native-resolution T1w images."""
    t1w_list: list[str] | None = None
    """List of T1w file paths identified for processing."""

    _layout = None

    _paths = (
        'bids_dir',
        'output_dir',
        'work_dir',
        'ncdlmuse_dir',
        'log_dir',
        'templateflow_home',
        'bids_database_dir',
    )

    @classmethod
    def init(cls):
        """Initialize BIDS layout and select subjects."""
        if cls.layout is not None:
            return  # Already initialized

        if not cls.bids_dir:
            # In non-BIDS mode, layout is None
            cls._layout = None
            return

        # If group analysis, skip BIDS layout initialization entirely.
        if cls.analysis_level == 'group':
            cls._layout = None
            return

        # --- The following is for non-group (participant) analysis only ---
        import re

        import bids.exceptions
        from bids.layout import BIDSLayout, BIDSLayoutIndexer

        # Determine and set the BIDS database path for BIDSLayout
        cls._db_path = None  # Initialize class attribute _db_path
        default_db_in_workdir = None
        if cls.work_dir and hasattr(cls, 'run_uuid') and cls.run_uuid:
            try:
                default_db_in_workdir = Path(cls.work_dir) / cls.run_uuid / 'bids_db'
            except TypeError:
                pass  # default_db_in_workdir remains None

        if cls.bids_database_dir:
            cls._db_path = Path(cls.bids_database_dir)
        elif default_db_in_workdir:
            cls._db_path = default_db_in_workdir
            # Create the default database directory if it's going to be used
            try:
                cls._db_path.mkdir(exist_ok=True, parents=True)
            except OSError as e:
                print(
                    f'WARNING: Could not create BIDS DB dir {cls._db_path}: {e}', file=sys.stderr
                )
                # Proceeding with cls._db_path as None if creation fails might be an option
                # or let BIDSLayout handle it if the path is then unusable.
                # For now, let's assume BIDSLayout will manage if path is None.

        # Recommended after PyBIDS 12.1
        indexer = BIDSLayoutIndexer(
            validate=not cls.skip_bids_validation,
            ignore=(
                'code',  # Irrelevant folders for BIDS Layout
                'stimuli',
                'sourcedata',
                'models',
                re.compile(r'^/\.\w+'),  # Hidden files/folders
            ),
        )

        # Setup the BIDSLayout
        try:
            cls._layout = BIDSLayout(
                str(cls.bids_dir),
                database_path=cls._db_path,  # cls._db_path is now robustly defined or None
                # Reset database if we are using an in-memory DB (cls._db_path is None)
                # OR if using a default path (bids_database_dir is None and _db_path is not None)
                reset_database=(cls._db_path is None)
                or (cls.bids_database_dir is None and cls._db_path is not None),
                indexer=indexer,
            )
            cls.bids_description_hash = cls._layout.description.__hash__()
            cls.bids_database_dir = cls._db_path

        except (bids.exceptions.PyBIDSException, OSError, ValueError, TypeError) as e:
            # Handle layout initialization errors
            print(f'ERROR: Could not index BIDS dataset: {e}', file=sys.stderr)
            cls._layout = None
            # Decide if this should be fatal or allow continuation
            # For now, let later checks handle missing layout if needed

        cls.layout = cls._layout

        if cls.bids_filters:
            from bids.layout import Query

            def _process_value(value):
                """Convert string with "Query" in it to Query object."""
                if isinstance(value, list):
                    return [_process_value(val) for val in value]
                else:
                    return (
                        getattr(Query, value[7:-4])
                        if not isinstance(value, Query) and 'Query' in value
                        else value
                    )

            # unserialize pybids Query enum values
            for acq, filters in cls.bids_filters.items():
                for k, v in filters.items():
                    cls.bids_filters[acq][k] = _process_value(v)

        if cls._layout and cls.participant_label is None:
            cls.participant_label = cls._layout.get_subjects()

        if 'all' in cls.debug:
            cls.debug = list(DEBUG_MODES)


# These variables are not necessary anymore
del _exec_env
del _nipype_ver
del _templateflow_home
del _tf_ver
del _free_mem_at_start
del _oc_limit
del _oc_policy


class workflow(_Config):
    """Configure the particular execution graph of this workflow."""

    # Add DLMUSE specific workflow options
    dlmuse_device = None
    """Device for NiChart_DLMUSE model inference ('cpu', 'cuda', 'mps')."""
    dlmuse_model_folder = None
    """Path to custom NiChart_DLMUSE model folder."""
    dlmuse_derived_roi_mappings_file = None
    """Path to derived ROI mappings file."""
    dlmuse_muse_roi_mappings_file = None
    """Path to MUSE ROI mappings file."""
    dlmuse_all_in_gpu = False
    """Load and run the entire DLMUSE model on GPU."""
    dlmuse_disable_tta = False
    """Disable Test-Time Augmentation for DLMUSE inference."""
    dlmuse_clear_cache = False
    """Clear the DLMUSE model download cache before running."""
    dlmuse_save_all_outputs = False
    """Save all intermediate NiChart_DLMUSE outputs (including raw outputs directory)."""

    @classmethod
    def init(cls):
        # Avoid additional runtime if not required
        pass  # No specific init needed for workflow section currently


class loggers:
    """Keep loggers easily accessible (see :py:func:`init`)."""

    _fmt = '%(asctime)s,%(msecs)d %(name)-2s %(levelname)-2s:\n\t %(message)s'
    _datefmt = '%y%m%d-%H:%M:%S'

    default = logging.getLogger()
    """The root logger."""
    cli = logging.getLogger('cli')
    """Command-line interface logging."""
    workflow = logging.getLogger('nipype.workflow')
    """NiPype's workflow logger."""
    interface = logging.getLogger('nipype.interface')
    """NiPype's interface logger."""
    utils = logging.getLogger('nipype.utils')
    """NiPype's utils logger."""

    @classmethod
    def init(cls):
        """
        Set the log level, initialize all loggers into :py:class:`loggers`.

            * Add new logger levels (25: IMPORTANT, and 15: VERBOSE).
            * Add a new sub-logger (``cli``).
            * Logger configuration.

        """
        from nipype import config as ncfg

        if not cls.cli.hasHandlers():
            _handler = logging.StreamHandler(stream=sys.stdout)
            _handler.setFormatter(logging.Formatter(fmt=cls._fmt, datefmt=cls._datefmt))
            cls.cli.addHandler(_handler)

        # Set levels for all loggers
        cls.default.setLevel(execution.log_level)
        cls.cli.setLevel(execution.log_level)
        cls.interface.setLevel(execution.log_level)
        cls.workflow.setLevel(execution.log_level)
        cls.utils.setLevel(execution.log_level)

        # Prevent nipype loggers from propagating to avoid duplicates
        cls.workflow.propagate = False
        cls.interface.propagate = False
        cls.utils.propagate = False

        # Add handlers to nipype loggers since they don't propagate anymore
        for nipype_logger in [cls.workflow, cls.interface, cls.utils]:
            if not nipype_logger.hasHandlers():
                _handler_nipype = logging.StreamHandler(stream=sys.stdout)
                _handler_nipype.setFormatter(logging.Formatter(fmt=cls._fmt, datefmt=cls._datefmt))
                nipype_logger.addHandler(_handler_nipype)

        # Configure nipype to use our logging setup
        ncfg.update_config(
            {'logging': {'log_directory': str(execution.log_dir), 'log_to_file': True}}
        )

    @classmethod
    def getLogger(cls, name):
        """Get a logger with the proper ncdlmuse configuration."""
        logger = logging.getLogger(name)
        logger.setLevel(execution.log_level)

        # Set up handler if it doesn't have one
        if not logger.hasHandlers():
            handler = logging.StreamHandler(stream=sys.stdout)
            handler.setFormatter(logging.Formatter(fmt=cls._fmt, datefmt=cls._datefmt))
            handler.setLevel(execution.log_level)
            logger.addHandler(handler)

        # Prevent propagation to avoid duplicates unless it's a nipype logger
        if not name.startswith('nipype'):
            logger.propagate = False

        return logger


class seeds(_Config):
    """Initialize the PRNG and track random seed assignments."""

    _random_seed = None
    master = None
    """Master random seed to initialize the Pseudorandom Number Generator (PRNG)."""
    ants = None
    """Seed used for antsRegistration, antsAI, antsMotionCorr."""
    numpy = None
    """Seed used by NumPy."""

    @classmethod
    def init(cls):
        """Initialize a seeds object."""
        if cls._random_seed is not None:
            cls.master = cls._random_seed
        if cls.master is None:
            cls.master = random.randint(1, 65536)
        random.seed(cls.master)  # initialize the PRNG
        # functions to set program specific seeds
        cls.ants = _set_ants_seed()
        cls.numpy = _set_numpy_seed()


def _set_ants_seed():
    """Fix random seed for antsRegistration, antsAI, antsMotionCorr."""
    val = random.randint(1, 65536)
    os.environ['ANTS_RANDOM_SEED'] = str(val)
    return val


def _set_numpy_seed():
    """NumPy's random seed is independent from Python's `random` module"""
    import numpy as np

    val = random.randint(1, 65536)
    np.random.seed(val)
    return val


def from_dict(settings, init=True, ignore=None):
    """Read settings from a flat dictionary.

    Arguments
    ---------
    setting : dict
        Settings to apply to any configuration
    init : `bool` or :py:class:`~collections.abc.Container`
        Initialize all, none, or a subset of configurations.
    ignore : :py:class:`~collections.abc.Container`
        Collection of keys in ``setting`` to ignore
    """

    # Accept global True/False or container of configs to initialize
    def initialize(x):
        return init if init in (True, False) else x in init

    nipype.load(settings, init=initialize('nipype'), ignore=ignore)
    execution.load(settings, init=initialize('execution'), ignore=ignore)
    workflow.load(settings, init=initialize('workflow'), ignore=ignore)
    seeds.load(settings, init=initialize('seeds'), ignore=ignore)

    loggers.init()


def load(filename, skip=None, init=True):
    """Load settings from file.

    Arguments
    ---------
    filename : :py:class:`os.PathLike`
        TOML file containing NCDLMUSE configuration.
    skip : dict or None
        Sets of values to ignore during load, keyed by section name
    init : `bool` or :py:class:`~collections.abc.Container`
        Initialize all, none, or a subset of configurations.
    """
    from toml import loads

    skip = skip or {}

    # Accept global True/False or container of configs to initialize
    def initialize(x):
        return init if init in (True, False) else x in init

    filename = Path(filename)
    settings = loads(filename.read_text())
    for sectionname, configs in settings.items():
        if sectionname != 'environment':
            section = getattr(sys.modules[__name__], sectionname)
            ignore = skip.get(sectionname)
            section.load(configs, ignore=ignore, init=initialize(sectionname))
    init_spaces()


def get(flat=False):
    """Get config as a dict."""
    settings = {
        'environment': environment.get(),
        'execution': execution.get(),
        'workflow': workflow.get(),
        'nipype': nipype.get(),
        'seeds': seeds.get(),
    }
    if not flat:
        return settings

    return {
        '.'.join((section, k)): v
        for section, configs in settings.items()
        for k, v in configs.items()
    }


def dumps():
    """Format config into toml."""
    from toml import dumps

    return dumps(get())


def to_filename(filename):
    """Write settings to file."""
    filename = Path(filename)
    filename.write_text(dumps())


def init_spaces(checkpoint=True):
    """Initialize the :attr:`~workflow.spaces` setting."""
    from niworkflows.utils.spaces import Reference, SpatialReferences

    spaces = execution.output_spaces or SpatialReferences()
    if not isinstance(spaces, SpatialReferences):
        spaces = SpatialReferences(
            [ref for s in spaces.split(' ') for ref in Reference.from_string(s)]
        )

    if checkpoint and not spaces.is_cached():
        spaces.checkpoint()

    # Add the default standard space if not already present (required by several sub-workflows)
    if 'MNI152NLin2009cAsym' not in spaces.get_spaces(nonstandard=False, dim=(3,)):
        spaces.add(Reference('MNI152NLin2009cAsym', {}))

    # Make the SpatialReferences object available
    workflow.spaces = spaces
