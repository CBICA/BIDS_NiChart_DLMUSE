# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""Utilities to handle BIDS inputs."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .. import config

LOGGER = config.loggers.getLogger('ncdlmuse.utils.bids')


def get_entities_from_file(file_path, layout=None):
    """Safely get BIDS entities from a file path using a layout.

    Parameters
    ----------
    file_path : str or :py:class:`~pathlib.Path`
        The path to the NIfTI file.
    layout : :py:class:`bids.layout.BIDSLayout` or None
        An initialized BIDSLayout. If None, attempts to create one,
        assuming the file is within a valid BIDS structure.

    Returns
    -------
    entities : dict
        A dictionary of BIDS entities found for the file.
        Returns a dictionary with default/placeholder values if parsing fails.

    Notes
    -----
    Creating a BIDSLayout for every file can be inefficient. It's best
    to pass an existing layout when possible.
    The fallback mechanism assumes a standard BIDS structure relative to the file
    (e.g., file is in ``<root>/sub-XX/ses-YY/anat/``), which might not always hold.

    """

    if layout:
        try:
            entities = layout.parse_file_entities(str(file_path))
            # Ensure essential entities have defaults if missing (though layout usually provides)
            entities.setdefault('subject', 'UNKNOWN')
            entities.setdefault('session', None)
            entities.setdefault('datatype', 'anat')  # Assume anat if missing
            entities.setdefault('suffix', Path(file_path).name.split('_')[-1].split('.')[0])
            return entities
        except (ValueError, IndexError, AttributeError, KeyError) as e:
            LOGGER.warning(
                f'Could not parse BIDS entities for {file_path} using provided layout: {e}'
            )
            # Fall through to fallback

    # Fallback: Try to create layout from file path (less robust)
    try:
        from bids.layout import BIDSLayout

        # Attempt to find BIDS root by navigating up. This is fragile!
        # Adjust the number of parents based on typical depth if needed
        possible_root = Path(file_path).parent.parent.parent  # Assumes anat/ses/sub structure
        layout = BIDSLayout(str(possible_root), validate=False)
        entities = layout.parse_file_entities(str(file_path))
        entities.setdefault('subject', 'UNKNOWN')
        entities.setdefault('session', None)
        entities.setdefault('datatype', 'anat')
        entities.setdefault('suffix', Path(file_path).name.split('_')[-1].split('.')[0])
        return entities
    except (ValueError, AttributeError, FileNotFoundError, ImportError) as e:
        LOGGER.warning(f'Could not parse BIDS entities for {file_path} via fallback layout: {e}')
        # Provide default/dummy values to avoid crashing downstream nodes
        return {
            'subject': Path(file_path).stem.split('_')[0].replace('sub-', '') or 'UNKNOWN',
            'session': None,
            'task': None,
            'run': None,
            'datatype': 'anat',
            'suffix': Path(file_path).name.split('_')[-1].split('.')[0],
            'desc': None,  # Add common optional entities
            'space': None,
        }


def _extract_entities_regex(file_path):
    """Extract basic BIDS entities using regex when layout parsing fails."""
    file_path = str(file_path)
    entities = {}

    # Extract subject ID
    subject_match = re.search(r'sub-([a-zA-Z0-9]+)', file_path)
    if subject_match:
        entities['subject'] = subject_match.group(1)

    # Extract session ID
    session_match = re.search(r'ses-([a-zA-Z0-9]+)', file_path)
    if session_match:
        entities['session'] = session_match.group(1)

    # Extract acquisition
    acq_match = re.search(r'acq-([a-zA-Z0-9]+)', file_path)
    if acq_match:
        entities['acquisition'] = acq_match.group(1)

    # Extract run
    run_match = re.search(r'run-(\d+)', file_path)
    if run_match:
        entities['run'] = run_match.group(1)

    # Extract echo
    echo_match = re.search(r'echo-(\d+)', file_path)
    if echo_match:
        entities['echo'] = echo_match.group(1)

    # Extract part
    part_match = re.search(r'part-(mag|phase|real|imag)', file_path)
    if part_match:
        entities['part'] = part_match.group(1)

    # Extract chunk
    chunk_match = re.search(r'chunk-(\d+)', file_path)
    if chunk_match:
        entities['chunk'] = chunk_match.group(1)

    # Extract contrast enhancement
    ce_match = re.search(r'ce-([a-zA-Z0-9]+)', file_path)
    if ce_match:
        entities['ce'] = ce_match.group(1)

    # Extract reconstruction
    rec_match = re.search(r'rec-([a-zA-Z0-9]+)', file_path)
    if rec_match:
        entities['reconstruction'] = rec_match.group(1)

    return entities


def collect_data(
    layout,
    participant_label,
    bids_filters=None,
):
    """Use pybids to retrieve the input data for a given participant."""
    queries = {
        't1w': {'datatype': 'anat', 'suffix': 'T1w'},
    }

    bids_filters = bids_filters or {}
    for acq, entities in bids_filters.items():
        queries[acq].update(entities)

    subj_data = {
        dtype: sorted(
            layout.get(
                return_type='file',
                subject=participant_label,
                extension=['nii', 'nii.gz'],
                **query,
            )
        )
        for dtype, query in queries.items()
    }

    return subj_data


def write_bidsignore(deriv_dir):
    """Write .bidsignore file."""
    bids_ignore = (
        '*.html',
        'logs/',
        'figures/',  # Reports
        '*_T1w.json',
    )
    ignore_file = Path(deriv_dir) / '.bidsignore'

    ignore_file.write_text('\n'.join(bids_ignore) + '\n')


def write_derivative_description(bids_dir, deriv_dir):
    """Write derivative dataset_description file."""
    from ncdlmuse.__about__ import DOWNLOAD_URL, __url__, __version__

    bids_dir = Path(bids_dir)
    deriv_dir = Path(deriv_dir)
    desc = {
        'Name': 'BIDS NiChart DLMUSE: BIDS-Apps wrapper for NiChart DLMUSE',
        'BIDSVersion': '1.10.0',
        'PipelineDescription': {
            'Name': 'BIDS_NiChart_DLMUSE',
            'Version': __version__,
            'CodeURL': DOWNLOAD_URL,
        },
        'CodeURL': __url__,
        'HowToAcknowledge': 'Please cite our paper doi: 10.1016/j.neuroimage.2015.11.073',
    }

    # Keys that can only be set by environment
    if 'NCDLMUSE_DOCKER_TAG' in os.environ:
        desc['DockerHubContainerTag'] = os.environ['NCDLMUSE_DOCKER_TAG']

    if 'NCDLMUSE_SINGULARITY_URL' in os.environ:
        singularity_url = os.environ['NCDLMUSE_SINGULARITY_URL']
        desc['SingularityContainerURL'] = singularity_url

        singularity_md5 = _get_shub_version(singularity_url)
        if singularity_md5 and singularity_md5 is not NotImplemented:
            desc['SingularityContainerMD5'] = _get_shub_version(singularity_url)

    # Keys deriving from source dataset
    orig_desc = {}
    fname = bids_dir / 'dataset_description.json'
    if fname.exists():
        with fname.open() as fobj:
            orig_desc = json.load(fobj)

    if 'DatasetDOI' in orig_desc:
        desc['SourceDatasetsURLs'] = [f'https://doi.org/{orig_desc["DatasetDOI"]}']

    if 'License' in orig_desc:
        desc['License'] = orig_desc['License']

    with (deriv_dir / 'dataset_description.json').open('w') as fobj:
        json.dump(desc, fobj, indent=4)


def _get_shub_version(singularity_url):
    return NotImplemented


def find_atlas_entities(filename):
    """Extract atlas entities from filename."""
    import os

    fname = os.path.basename(filename)
    elements = fname.split('_')

    out = []
    for ent in ('tpl', 'atlas', 'res'):
        ent_parts = [el for el in elements if el.startswith(f'{ent}-')]
        ent_value = None
        if ent_parts:
            ent_value = ent_parts[0].split('-')[1]

        out.append(ent_value)

    suffix = elements[-1].split('.')[0]
    extension = '.' + '.'.join(elements[-1].split('.')[1:])
    out += [suffix, extension]

    return tuple(out)
