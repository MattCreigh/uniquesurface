"""Tests for the atomic file write helpers."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from trinity.atomic import atomic_write_bytes, atomic_write_text


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
