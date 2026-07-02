"""Tests for the lock-screen wallpaper backend."""

from __future__ import annotations

from pathlib import Path

import pytest

from usurface.backends.lock import LockBackend
from usurface.manifest import Manifest


@pytest.fixture
def fake_kwriteconfig(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    from usurface.backends import _kconfig as kc

    def fake(*, file, group, key, value, type_=None, dry_run=False):  # type: ignore[no-untyped-def]
        argv = [
            "kwriteconfig6",
            "--file",
            str(file),
            "--group",
            group,
            "--key",
            key,
        ]
        if type_:
            argv.extend(["--type", type_])
        argv.append(value)
        calls.append(argv)
        return argv

    monkeypatch.setattr(kc, "kwriteconfig", fake)

    def fake_nested(*, file, group_path, key, value):  # type: ignore[no-untyped-def]
        argv = ["kwriteconfig6", "--file", str(file)]
        for g in group_path:
            argv.extend(["--group", g])
        argv.extend(["--key", key, "--type", "string", value])
        calls.append(argv)

    monkeypatch.setattr("usurface.backends.lock._kwriteconfig_nested", fake_nested)
    return calls


def test_lock_writes_wallpaper_plugin_key(
    fake_kwriteconfig: list[list[str]],
) -> None:
    """The kscreenlocker kcfg maps wallpaperPluginId to the INI key
    ``WallpaperPlugin``. The backend must write this key."""
    backend = LockBackend()
    manifest = Manifest()
    backend.apply(manifest, Path("/tmp/wall.jpg"))
    groups = [" ".join(c) for c in fake_kwriteconfig]
    assert any("WallpaperPlugin" in g and "org.kde.image" in g for g in groups)


def test_lock_writes_nested_image_key(
    fake_kwriteconfig: list[list[str]],
) -> None:
    """The org.kde.image plugin reads Image from
    [Greeter][Wallpaper][org.kde.image][General]."""
    backend = LockBackend()
    manifest = Manifest()
    backend.apply(manifest, Path("/tmp/wall.jpg"))
    groups = [" ".join(c) for c in fake_kwriteconfig]
    assert any(
        "Greeter" in g
        and "Wallpaper" in g
        and "org.kde.image" in g
        and "General" in g
        and "Image" in g
        for g in groups
    )


def test_lock_dry_run_plan_includes_nested() -> None:
    backend = LockBackend()
    plan = backend.dry_run_plan(Path("/tmp/wall.jpg"))
    assert any("WallpaperPlugin" in line for line in plan)
    assert any("Wallpaper" in line and "General" in line for line in plan)
