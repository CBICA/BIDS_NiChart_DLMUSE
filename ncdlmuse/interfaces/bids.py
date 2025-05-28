"""Adapted interfaces from Niworkflows."""

from json import loads

from bids.layout import Config
from nipype.interfaces.base import (
    BaseInterfaceInputSpec,
    OutputMultiObject,
    SimpleInterface,
    Str,
    TraitedSpec,
    traits,
)
from niworkflows.interfaces.bids import DerivativesDataSink as BaseDerivativesDataSink

from ncdlmuse.data import load as load_data

# NOTE: Modified for ncdlmuse's purposes
ncdlmuse_spec = loads(load_data.readable('ncdlmuse_bids_config.json').read_text())
bids_config = Config.load('bids')
deriv_config = Config.load('derivatives')

ncdlmuse_entities = {v['name']: v['pattern'] for v in ncdlmuse_spec['entities']}
merged_entities = {**bids_config.entities, **deriv_config.entities}
merged_entities = {k: v.pattern for k, v in merged_entities.items()}
merged_entities = {**merged_entities, **ncdlmuse_entities}
merged_entities = [{'name': k, 'pattern': v} for k, v in merged_entities.items()]
config_entities = frozenset({e['name'] for e in merged_entities})


class _BIDSDataGrabberInputSpec(BaseInterfaceInputSpec):
    subject_data = traits.Dict(Str, traits.Any)
    subject_id = Str()


class _BIDSDataGrabberOutputSpec(TraitedSpec):
    out_dict = traits.Dict(desc='output data structure')
    t1w = OutputMultiObject(desc='output T1w images')


class BIDSDataGrabber(SimpleInterface):
    """Collect T1w files from a BIDS directory structure.

    .. testsetup::

        >>> data_dir_canary()

    >>> bids_src = BIDSDataGrabber()
    >>> bids_src.inputs.subject_data = bids_collect_data(
    ...     str(datadir / 'ds114'), '01', bids_validate=False)[0]
    >>> bids_src.inputs.subject_id = '01'
    >>> res = bids_src.run()
    >>> res.outputs.t1w  # doctest: +ELLIPSIS +NORMALIZE_WHITESPACE
    ['.../ds114/sub-01/ses-retest/anat/sub-01_ses-retest_T1w.nii.gz',
     '.../ds114/sub-01/ses-test/anat/sub-01_ses-test_T1w.nii.gz']

    """

    input_spec = _BIDSDataGrabberInputSpec
    output_spec = _BIDSDataGrabberOutputSpec

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _run_interface(self, runtime):
        bids_dict = self.inputs.subject_data

        self._results['out_dict'] = bids_dict
        self._results['t1w'] = bids_dict.get('t1w', [])

        if not self._results['t1w']:
            raise FileNotFoundError(
                f'No T1w images found for subject sub-{self.inputs.subject_id}'
            )

        return runtime


class DerivativesDataSink(BaseDerivativesDataSink):
    """Store derivative files.

    A child class of the niworkflows DerivativesDataSink, using ncdlmuse's configuration files.
    """

    out_path_base = ''
    _allowed_entities = set(config_entities)
    _config_entities = config_entities
    _config_entities_dict = merged_entities
    _file_patterns = ncdlmuse_spec['default_path_patterns']


class OverrideDerivativesDataSink:
    """A context manager for temporarily overriding the definition of DerivativesDataSink.

    Parameters
    ----------
    None

    Attributes
    ----------
    original_class (type): The original class that is replaced during the override.

    Methods
    -------
    __init__()
        Initialize the context manager.
    __enter__()
        Enters the context manager and performs the class override.
    __exit__(exc_type, exc_value, traceback)
        Exits the context manager and restores the original class definition.
    """

    def __init__(self, module):
        """Initialize the context manager with the target module.

        Parameters
        -----------
        module
            The module where SomeClass should be overridden.
        """
        self.module = module

    def __enter__(self):
        """Enter the context manager and perform the class override.

        Returns
        -------
        OverrideConfoundsDerivativesDataSink
            The instance of the context manager.
        """
        # Save the original class
        self.original_class = self.module.DerivativesDataSink
        # Replace SomeClass with YourOwnClass
        self.module.DerivativesDataSink = DerivativesDataSink
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the context manager and restore the original class definition.

        Parameters
        ----------
        exc_type : type
            The type of the exception (if an exception occurred).
        exc_value : Exception
            The exception instance (if an exception occurred).
        traceback : traceback
            The traceback information (if an exception occurred).

        Returns
        -------
        None
        """
        # Restore the original class
        self.module.DerivativesDataSink = self.original_class
