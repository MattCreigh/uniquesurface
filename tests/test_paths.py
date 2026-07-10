"""Tests for the XDG-aware paths module."""

from __future__ import annotations

from pathlib import Path

import pytest

from trinity import paths


def test_config_dir_under_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert paths.config_dir() == tmp_path / "trinity"


def test_state_dir_under_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert paths.state_dir() == tmp_path / "trinity"


def test_cache_dir_under_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert paths.cache_dir() == tmp_path / "trinity"


def test_shared_dir_uses_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    custom = tmp_path / "custom-share"
    monkeypatch.setenv("TRINITY_SHARED_DIR", str(custom))
    assert paths.shared_wallpapers_dir() == custom
    assert paths.shared_wallpaper() == custom / "last_wallpaper.jpg"


def test_shared_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRINITY_SHARED_DIR", raising=False)
    assert paths.shared_wallpapers_dir() == __import__("pathlib").Path(
        "/usr/local/share/wallpapers"
    )
