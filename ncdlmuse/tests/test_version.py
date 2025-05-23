"""Test version checks."""

import datetime
from pathlib import Path

import pytest
from packaging.version import Version

from ncdlmuse.cli import version as _version
from ncdlmuse.cli.version import DATE_FMT, check_latest, is_flagged, requests


class MockResponse:
    """Mocks the requests module so that Pypi is not actually queried."""

    status_code = 200
    _json = {'releases': {'1.0.0': None, '1.0.1': None, '1.1.0': None, '1.1.1rc1': None}}

    def __init__(self, code=200, json=None):
        """Allow setting different response codes."""
        self.status_code = code
        if json is not None:
            self._json = json

    def json(self):
        """Redefine the response object."""
        return self._json


@pytest.mark.parametrize(
    ('result', 'code', 'json'),
    [
        (None, 404, None),
        (None, 200, {'releases': {'1.0.0rc1': None}}),
        (Version('1.1.0'), 200, None),
        (Version('1.0.0'), 200, {'releases': {'1.0.0': None}}),
    ],
)
def test_check_latest2(tmpdir, monkeypatch, result, code, json):
    """Test latest version check with varying server responses."""
    tmpdir.chdir()
    monkeypatch.setenv('HOME', str(tmpdir))
    assert str(Path.home()) == str(tmpdir)

    def mock_get(*args, **kwargs):
        return MockResponse(code=code, json=json)

    monkeypatch.setattr(requests, 'get', mock_get)

    v = check_latest()
    if result is None:
        assert v is None
    else:
        assert isinstance(v, Version)
        assert v == result


@pytest.mark.parametrize(
    'bad_cache',
    [
        '3laj#r???d|3akajdf#',
        '2.0.0|3akajdf#',
        '|'.join(
            (
                '2.0.0',
                datetime.datetime.now(tz=datetime.timezone.utc).strftime(DATE_FMT),
                '',
            )
        ),
        '',
    ],
)
def test_check_latest3(tmpdir, monkeypatch, bad_cache):
    """Test latest version check when the cache file is corrupted."""
    tmpdir.chdir()
    monkeypatch.setenv('HOME', str(tmpdir))
    assert str(Path.home()) == str(tmpdir)

    def mock_get(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr(requests, 'get', mock_get)

    # Initially, cache should not exist
    cachefile = Path.home() / '.cache' / 'ncdlmuse' / 'latest'
    cachefile.parent.mkdir(parents=True, exist_ok=True)
    assert not cachefile.exists()

    cachefile.write_text(bad_cache)
    v = check_latest()
    assert isinstance(v, Version)
    assert v == Version('1.1.0')


@pytest.mark.parametrize(
    ('result', 'version', 'code', 'json'),
    [
        (False, '1.2.1', 200, {'flagged': {'1.0.0': None}}),
        (True, '1.2.1', 200, {'flagged': {'1.2.1': None}}),
        (True, '1.2.1', 200, {'flagged': {'1.2.1': 'FATAL Bug!'}}),
        (False, '1.2.1', 404, {'flagged': {'1.0.0': None}}),
        (False, '1.2.1', 200, {'flagged': []}),
        (False, '1.2.1', 200, {}),
    ],
)
def test_is_flagged(monkeypatch, result, version, code, json):
    """Test that the flagged-versions check is correct."""
    monkeypatch.setattr(_version, '__version__', version)

    def mock_get(*args, **kwargs):
        return MockResponse(code=code, json=json)

    monkeypatch.setattr(requests, 'get', mock_get)

    val, reason = is_flagged()
    assert val is result

    test_reason = None
    if val:
        test_reason = json.get('flagged', {}).get(version, None)

    if test_reason is not None:
        assert reason == test_reason
    else:
        assert reason is None
