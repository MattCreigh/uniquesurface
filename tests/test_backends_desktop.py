"""Tests for the desktop wallpaper backend."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from usurface.backends.desktop import DesktopBackend
from usurface.backends._kconfig import KConfigToolMissing
from usurface.manifest import Manifest


@pytest.fixture
def fake_kwriteconfig(monkeypatch: pytest.MonkeyPatch):
    """Capture kwriteconfig invocations without actually invoking the tool."""
    calls: list[list[str]] = []

    def fake_kwriteconfig(*, file, group, key, value, type_=None, dry_run=False):  # type: ignore[no-untyped-def]
        argv = ["kwriteconfig6", "--file", str(file), "--group", group, "--key", key]
        if type_:
            argv += ["--type", type_]
        argv.append(value)
        calls.append(argv)
        return argv

    from usurface.backends import _kconfig

    monkeypatch.setattr(_kconfig, "kwriteconfig", fake_kwriteconfig)
    yield calls


@pytest.fixture
def fake_qdbus(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    from usurface.backends import _kconfig

    def fake_qdbus(*, service, path, method, args=(), dry_run=False):  # type: ignore[no-untyped-def]
        argv = ["qdbus6", service, path, method, *args]
        calls.append(argv)
        return argv

    monkeypatch.setattr(_kconfig, "qdbus_call", fake_qdbus)
    yield calls


def test_desktop_apply_writes_expected_keys(
    fake_kwriteconfig: list[list[str]],
    fake_qdbus: list[list[str]],
    tmp_path: Path,
) -> None:
    target_image = tmp_path / "wp.jpg"
    target_image.write_bytes(b"\xff\xd8\xff" + b"data")

    backend = DesktopBackend()
    manifest = Manifest(tmp_path / "manifest.jsonl")
    backend.apply(manifest, target_image)

    # Two kwriteconfig calls (plugin + image).
    assert len(fake_kwriteconfig) == 2
    plugin_argv, image_argv = fake_kwriteconfig
    assert "wallpaperplugin" in plugin_argv
    assert "Image" in image_argv
    # Image arg uses a file:// URI.
    assert any(arg.startswith("file://") for arg in image_argv)
    # qdbus refreshWallpaper was called.
    assert len(fake_qdbus) == 1
    assert "refreshWallpaper" in fake_qdbus[0]

    # Manifest entry recorded for the config file.
    entries = manifest.iter_entries()
    assert len(entries) == 1
    assert entries[0].path == str(Path("~/.config/plasma-org.kde.plasma.desktop-appletsrc").expanduser())



def test_desktop_dry_run_plan(
    fake_kwriteconfig: list[list[str]],
    fake_qdbus: list[list[str]],
    tmp_path: Path,
) -> None:
    target_image = tmp_path / "wp.jpg"
    target_image.write_bytes(b"\xff\xd8\xff" + b"data")
    plan = DesktopBackend().dry_run_plan(target_image)
    assert any("wallpaperplugin" in line for line in plan)
    assert any("Image" in line for line in plan)
    assert any("refreshWallpaper" in line for line in plan)
    # Dry run must not actually call anything.
    assert fake_kwriteconfig == []
    assert fake_qdbus == []
