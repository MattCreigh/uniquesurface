"""Tests for the lock-screen wallpaper backend."""

from __future__ import annotations

from pathlib import Path

import pytest

from usurface.backends.lock import LockBackend
from usurface.backends import _kconfig
from usurface.manifest import Manifest


@pytest.fixture
def fake_kwriteconfig(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    from usurface.backends import _kconfig as kc

    def fake(*, file, group, key, value, type_=None, dry_run=False):  # type: ignore[no-untyped-def]
        argv = ["kwriteconfig6", "--file", str(file), "--group", group, "--key", key]
        if type_:
            argv += ["--type", type_]
        argv.append(value)
        calls.append(argv)
        return argv

    monkeypatch.setattr(kc, "kwriteconfig", fake)
    yield calls


def test_lock_apply_writes_greeter_keys(
    fake_kwriteconfig: list[list[str]], tmp_path: Path
) -> None:
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"\xff\xd8\xff" + b"x")
    backend = LockBackend()
    backend.apply(Manifest(tmp_path / "manifest.jsonl"), target)

    assert len(fake_kwriteconfig) == 2
    plugin_argv, image_argv = fake_kwriteconfig
    assert "Theme" in plugin_argv
    assert "Image" in image_argv
    assert any(arg.startswith("file://") for arg in image_argv)

    # Manifest entry recorded for the config file.
    m = Manifest(tmp_path / "manifest.jsonl")
    entries = m.iter_entries()
    assert len(entries) == 1
    assert entries[0].path == str(Path("~/.config/kscreenlockerrc").expanduser())



def test_lock_dry_run_does_not_call(fake_kwriteconfig: list[list[str]], tmp_path: Path) -> None:
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"\xff\xd8\xff" + b"x")
    plan = LockBackend().dry_run_plan(target)
    assert any("Theme" in line for line in plan)
    assert any("Image" in line for line in plan)
    assert fake_kwriteconfig == []
