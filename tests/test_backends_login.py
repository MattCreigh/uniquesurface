"""Tests for the login-screen theme.conf editor."""

from __future__ import annotations

from pathlib import Path

import pytest

from usurface.backends.login import LoginBackend
from usurface.manifest import Manifest


@pytest.fixture
def fake_theme_conf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the login backend at a tmp theme.conf."""
    conf = tmp_path / "breeze" / "theme.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text(
        "[General]\ntype=image\nbackground=/old/path.jpg\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("usurface.backends.login._THEME_CONF_PATH", conf)
    return conf


def test_login_appends_when_no_background_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = tmp_path / "breeze" / "theme.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("[General]\ntype=image\n", encoding="utf-8")
    monkeypatch.setattr("usurface.backends.login._THEME_CONF_PATH", conf)

    backend = LoginBackend()
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"x")
    backend.apply(Manifest(tmp_path / "manifest.jsonl"), target)

    text = conf.read_text(encoding="utf-8")
    assert "background=" in text
    assert str(target.resolve()) in text


def test_login_replaces_existing_background_line(
    fake_theme_conf: Path, tmp_path: Path
) -> None:
    backend = LoginBackend()
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"x")
    backend.apply(Manifest(tmp_path / "manifest.jsonl"), target)

    text = fake_theme_conf.read_text(encoding="utf-8")
    assert "background=" in text
    assert "/old/path.jpg" not in text
    assert str(target.resolve()) in text
    # type=image line is preserved.
    assert "type=image" in text


def test_login_dry_run_does_not_modify(fake_theme_conf: Path, tmp_path: Path) -> None:
    backend = LoginBackend()
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"x")
    plan = backend.dry_run_plan(target)
    assert any("theme.conf" in line for line in plan)
    text = fake_theme_conf.read_text(encoding="utf-8")
    assert "/old/path.jpg" in text


def test_login_skips_when_theme_conf_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "does-not-exist.conf"
    monkeypatch.setattr("usurface.backends.login._THEME_CONF_PATH", missing)
    backend = LoginBackend()
    plan = backend.dry_run_plan(tmp_path / "wp.jpg")
    assert plan and plan[0].startswith("#")
    backend.apply(
        Manifest(tmp_path / "manifest.jsonl"), tmp_path / "wp.jpg"
    )  # must not raise
