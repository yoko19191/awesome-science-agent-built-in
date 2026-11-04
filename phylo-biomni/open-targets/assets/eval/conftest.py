"""Shared fixtures and helpers for Open Targets offline eval tests.

Tests invoke production code (scripts/open_targets_client.py, queries.py,
semantics.py) in fresh temporary directories.  Network calls are mocked to
return frozen fixture data — no live API access.
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add scripts/ to path so tests can import production code
PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = PKG_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

FIXTURES_DIR = PKG_ROOT / "assets" / "fixtures"


def load_fixture(name: str) -> dict | str:
    """Load a fixture file by name."""
    path = FIXTURES_DIR / name
    text = path.read_text(encoding="utf-8")
    if name.endswith(".json"):
        return json.loads(text)
    return text


class MockResponse:
    """Mock urllib response context manager."""

    def __init__(self, data: bytes, status: int = 200):
        self._data = data
        self._status = status
        self._reader = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @property
    def status(self):
        return self._status

    def read(self, n=-1):
        return self._reader.read(n) if n > 0 else self._reader.read()


def make_mock_urlopen(fixture_name: str, status: int = 200):
    """Create a mock urlopen that returns fixture data."""
    if fixture_name.endswith(".json"):
        data = json.dumps(load_fixture(fixture_name)).encode("utf-8")
    else:
        data = load_fixture(fixture_name).encode("utf-8")
    return MagicMock(return_value=MockResponse(data, status))


def make_mock_urlopen_raw(raw_bytes: bytes, status: int = 200):
    """Create a mock urlopen that returns raw bytes."""
    return MagicMock(return_value=MockResponse(raw_bytes, status))


@pytest.fixture
def tmp_results(tmp_path, monkeypatch):
    """Provide a fresh temporary results directory."""
    monkeypatch.setenv("BIOMNI_RESULTS", str(tmp_path))
    return tmp_path
