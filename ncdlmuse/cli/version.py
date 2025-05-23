# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
#
# Copyright The NiPreps Developers <nipreps@gmail.com>
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
"""Version CLI helpers."""

from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

import requests

from .. import __version__

RELEASE_EXPIRY_DAYS = 14
DATE_FMT = '%Y%m%d'


def check_latest():
    """Determine whether this is the latest version."""
    from packaging.version import InvalidVersion, Version

    latest = None
    date = None
    outdated = None
    now = datetime.now(tz=timezone.utc)
    cachefile = Path.home() / '.cache' / 'ncdlmuse' / 'latest'
    try:
        cachefile.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        cachefile = None

    if cachefile and cachefile.exists():
        try:
            latest, date = cachefile.read_text().split('|')
        except Exception:  # noqa: S110, BLE001
            pass
        else:
            try:
                latest = Version(latest)
                date = datetime.strptime(date, DATE_FMT).astimezone(timezone.utc)
            except (InvalidVersion, ValueError):
                latest = None
            else:
                if abs((now - date).days) > RELEASE_EXPIRY_DAYS:
                    outdated = True

    if latest is None or outdated is True:
        response = None
        with suppress(Exception):
            response = requests.get(url='https://pypi.org/pypi/ncdlmuse/json', timeout=1.0)

        if response and response.status_code == 200:
            versions = [Version(rel) for rel in response.json()['releases'].keys()]
            versions = [rel for rel in versions if not rel.is_prerelease]
            if versions:
                latest = sorted(versions)[-1]
        else:
            latest = None

    if cachefile is not None and latest is not None:
        with suppress(OSError):
            cachefile.write_text(f'{latest}|{now.strftime(DATE_FMT)}')

    return latest


def is_flagged():
    """Check whether current version is flagged."""
    # https://raw.githubusercontent.com/CBICA/BIDS_NiChart_DLMUSE/main/.versions.json
    flagged = ()
    response = None
    with suppress(Exception):
        response = requests.get(
            url="""\
https://raw.githubusercontent.com/CBICA/BIDS_NiChart_DLMUSE/main/.versions.json""",
            timeout=1.0,
        )

    if response and response.status_code == 200:
        flagged = response.json().get('flagged', {}) or {}

    if __version__ in flagged:
        return True, flagged[__version__]

    return False, None
