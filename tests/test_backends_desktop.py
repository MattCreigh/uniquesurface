"""Tests for the desktop wallpaper backend."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from usurface.backends.desktop import DesktopBackend
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

    def fake_kwriteconfig_nested(*, file, group_path, key, value, type_=None, dry_run=False):  # type: ignore[no-untyped-def]
        argv = ["kwriteconfig6", "--file", str(file)]
        for g in group_path:
            argv += ["--group", g]
        argv += ["--key", key]
        if type_:
            argv += ["--type", type_]
        argv.append(value)
        calls.append(argv)
        return argv

    from usurface.backends import _kconfig

    monkeypatch.setattr(_kconfig, "kwriteconfig", fake_kwriteconfig)
    monkeypatch.setattr(_kconfig, "kwriteconfig_nested", fake_kwriteconfig_nested)
    yield calls


@pytest.fixture
def fake_qdbus(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    from usurface.backends import _kconfig

    def fake_qdbus(*, service, path, method, args=(), dry_run=False):  # type: ignore[no-untyped-def]
        argv = ["qdbus6", service, path, method, *args]
        calls.append(argv)
        return argv

    def fake_evaluate(*, image_uri, plugin="org.kde.image", dry_run=False):  # type: ignore[no-untyped-def]
        argv = [
            "qdbus6",
            "org.kde.plasmashell",
            "/PlasmaShell",
            "org.kde.PlasmaShell.evaluateScript",
            f"<set Image={image_uri} plugin={plugin}>",
        ]
        calls.append(argv)
        return argv

    monkeypatch.setattr(_kconfig, "qdbus_call", fake_qdbus)
    monkeypatch.setattr(_kconfig, "evaluate_wallpaper_script", fake_evaluate)
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

    # At minimum the flat [Containments] group writes (plugin + image).
    assert len(fake_kwriteconfig) >= 2
    plugin_argv = next(c for c in fake_kwriteconfig if "wallpaperplugin" in c)
    image_argv = next(c for c in fake_kwriteconfig if c[-2] == "Image")
    assert "org.kde.image" in plugin_argv
    # Image arg uses a file:// URI.
    assert any(arg.startswith("file://") for arg in image_argv)

    # The live apply uses the PlasmaShell evaluateScript D-Bus method on
    # the *real* Plasma 6 service (org.kde.plasmashell), not the legacy
    # org.kde.plasma.desktop / refreshWallpaper call that does not exist.
    assert any("org.kde.plasmashell" in c for c in fake_qdbus)
    assert any(any("evaluateScript" in arg for arg in c) for c in fake_qdbus)
    # The evaluateScript argv must carry the file:// URI of the wallpaper.
    eval_call = next(c for c in fake_qdbus if any("evaluateScript" in arg for arg in c))
    assert any("file://" in arg for arg in eval_call)

    # Manifest entry recorded for the config file.
    entries = manifest.iter_entries()
    assert len(entries) == 1
    assert entries[0].path == str(
        Path(os.environ["XDG_CONFIG_HOME"]) / "plasma-org.kde.plasma.desktop-appletsrc"
    )


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
    # The live apply must use the evaluateScript D-Bus method on the real
    # Plasma 6 service. The old refreshWallpaper call does not exist.
    assert any("evaluateScript" in line for line in plan)
    assert any("org.kde.plasmashell" in line for line in plan)
    # Dry run must not actually call anything.
    assert fake_kwriteconfig == []
    assert fake_qdbus == []


def test_desktop_apply_writes_nested_containment_group(
    fake_kwriteconfig: list[list[str]],
    fake_qdbus: list[list[str]],
    tmp_path: Path,
) -> None:
    """When the appletsrc already has a desktop containment with a
    wallpaperplugin, the backend must write the nested
    ``[Containments][<id>][Wallpaper][org.kde.image][General] Image=``
    group that Plasma actually reads — not just the flat [Containments]
    group that Plasma ignores for wallpaper purposes."""
    import os

    # Pre-populate the appletsrc with a real containment structure.
    appletsrc = (
        Path(os.environ["XDG_CONFIG_HOME"])
        / "plasma-org.kde.plasma.desktop-appletsrc"
    )
    appletsrc.parent.mkdir(parents=True, exist_ok=True)
    appletsrc.write_text(
        "[Containments][1]\n"
        "plugin=org.kde.plasma.folder\n"
        "wallpaperplugin=org.kde.image\n"
        "lastScreen=0\n"
        "\n"
        "[Containments][1][Wallpaper][org.kde.image][General]\n"
        "Image=file:///old/wallpaper.jpg\n"
        "FillMode=2\n"
        "\n"
        "[Containments][2]\n"
        "plugin=org.kde.panel\n"
        "wallpaperplugin=org.kde.image\n"
    )

    target_image = tmp_path / "wp.jpg"
    target_image.write_bytes(b"\xff\xd8\xff" + b"data")
    backend = DesktopBackend()
    manifest = Manifest(tmp_path / "manifest.jsonl")
    backend.apply(manifest, target_image)

    # The nested containment write must target [Containments][1][Wallpaper]
    # [org.kde.image][General] — the group Plasma's wallpaper plugin reads.
    nested_calls = [
        c for c in fake_kwriteconfig if "Containments" in c and "Wallpaper" in c
    ]
    assert any("1" in c and "General" in c for c in nested_calls), nested_calls
    # The Image value must be the new wallpaper's file:// URI.
    image_nested = next(
        c for c in nested_calls if c[-2] == "Image" and "file://" in c[-1]
    )
    assert str(target_image) in image_nested[-1]
