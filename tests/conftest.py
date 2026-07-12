"""Shared pytest fixtures for trinity tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import respx

from trinity.providers.builtin import _http


@pytest.fixture(autouse=True)
def _isolate_xdg_env(tmp_path, monkeypatch):
    """Redirect XDG dirs to a tmp path so tests never touch real config.

    ``XDG_RUNTIME_DIR`` is included so ``systemd.is_paused`` never reads
    the developer's real ``/run/user/<uid>`` mask symlinks.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg_cache"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg_runtime"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Provide a writable "shared" wallpaper dir under tmp for tests.
    monkeypatch.setenv("TRINITY_SHARED_DIR", str(tmp_path / "shared_wallpapers"))
    yield


@pytest.fixture
def home_dir() -> Path:
    """Return the per-test HOME directory created by the autouse fixture."""
    return Path(os.environ["HOME"])


@pytest.fixture
def respx_mock():
    """A respx mock router scoped to the test."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture(autouse=True)
def _no_dns_pinning(monkeypatch):
    """Disable the pre-flight DNS safety check in ``_http`` for tests.

    respx mocks by hostname and the mock hosts don't resolve.
    Production code still runs the SSRF pre-flight — this is purely a
    test isolation aid.
    """
    monkeypatch.setattr(_http, "_resolve_safely_hook", lambda host: host)
