# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""Base module variables."""

try:
    from ncdlmuse._version import __version__
except ImportError:
    __version__ = '0+unknown'

__packagename__ = 'BIDS_NiChart_DLMUSE'
__copyright__ = 'Copyright 2025, The NCDLMUSE Developers'
__credits__ = ()
__url__ = 'https://github.com/CBICA/BIDS_NiChart_DLMUSE'

DOWNLOAD_URL = f'https://github.com/CBICA/{__packagename__}/archive/{__version__}.tar.gz'
