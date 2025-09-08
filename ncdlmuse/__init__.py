# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""NCDLMUSE: A BIDS app wrapper for NiChart_DLMUSE."""

import warnings

# Suppress pynvml deprecation warning from PyTorch CUDA initialization
warnings.filterwarnings('ignore', message='The pynvml package is deprecated')

try:
    from ncdlmuse._version import __version__
except ImportError:
    __version__ = '0+unknown'

# Keep other imports from __about__ if they are still relevant
from ncdlmuse.__about__ import __copyright__, __credits__, __packagename__  # noqa: E402

__all__ = [
    '__copyright__',
    '__credits__',
    '__packagename__',
    '__version__',
]
