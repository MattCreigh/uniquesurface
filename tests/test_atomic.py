"""Tests for the atomic file write helpers."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest

from trinity.atomic import atomic_replace_with, atomic_write_bytes, atomic_write_text


def test_atomic_write_bytes_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes(target, b"hello", mode=0o644)
    assert target.read_bytes() == b"hello"
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o644


def test_atomic_write_bytes_respects_mode_on_existing_file(
    tmp_path: Path,
) -> None:
    """A pre-existing file with restrictive permissions must end up with
    the requested mode after an atomic write — ``os.replace`` preserves
    the old mode, so the writer must chmod after the move."""
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")
    os.chmod(target, 0o600)
    atomic_write_bytes(target, b"new", mode=0o644)
    assert target.read_bytes() == b"new"
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o644, f"expected 0o644, got {oct(mode)}"


def test_atomic_write_text_applies_mode(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    target.write_text("old")
    os.chmod(target, 0o600)
    atomic_write_text(target, "new content", mode=0o644)
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o644
    assert target.read_text() == "new content"


def test_atomic_write_bytes_no_mode_preserves_existing(
    tmp_path: Path,
) -> None:
    """When no mode is given, the existing file's mode is preserved."""
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")
    os.chmod(target, 0o600)
    atomic_write_bytes(target, b"new")
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600
    assert target.read_bytes() == b"new"


def test_atomic_write_bytes_exdev_falls_back_to_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the temp dir and destination are on different filesystems
    (os.replace raises EXDEV), the writer falls back to a sibling temp
    file in the destination directory and still lands atomically."""
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")
    real_replace = os.replace
    calls = {"n": 0}

    def exdev_once(src: object, dst: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", exdev_once)
    atomic_write_bytes(target, b"new", mode=0o644)
    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o644
    # No temp files leaked next to the destination.
    leftovers = [p for p in tmp_path.iterdir() if p.name != "out.bin"]
    assert leftovers == []


def test_atomic_write_bytes_cleans_tmp_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the move fails, the temp file is removed (no /tmp litter)."""
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    def boom(src: object, dst: object) -> None:
        raise OSError(errno.EIO, "disk on fire")

    monkeypatch.setattr(os, "replace", boom)
    target = tmp_path / "sub" / "out.bin"
    with pytest.raises(OSError):
        atomic_write_bytes(target, b"data")
    # Only the (empty) destination dir remains; no .tmp litter anywhere.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "sub"]
    assert leftovers == []


def test_atomic_replace_with_writer_callback(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"

    def writer(f) -> None:  # type: ignore[no-untyped-def]
        f.write(b"streamed")

    atomic_replace_with(target, writer=writer)
    assert target.read_bytes() == b"streamed"
