"""Tests for the atomic write helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from usurface.atomic import (
    atomic_replace_with,
    atomic_write_bytes,
    atomic_write_text,
)


def test_writes_bytes_and_replaces(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes(target, b"hello world")
    assert target.read_bytes() == b"hello world"


def test_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")
    atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"


def test_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "out.bin"
    atomic_write_bytes(target, b"data")
    assert target.read_bytes() == b"data"


def test_text_helper_encodes_utf8(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    atomic_write_text(target, "héllo 👋")
    assert target.read_text(encoding="utf-8") == "héllo 👋"


def test_applies_mode(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes(target, b"x", mode=0o640)
    mode = stat_mode(target)
    assert mode == 0o640


def test_no_leftover_tmp_files_on_success(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes(target, b"x")
    leftovers = [
        p
        for p in tmp_path.iterdir()
        if p.name.startswith(".out.") and p.name.endswith(".tmp")
    ]
    assert leftovers == []


def test_replaces_via_writer(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")

    def write(f: object) -> None:
        assert hasattr(f, "write")
        f.write(b"new-content")  # type: ignore[attr-defined]

    atomic_replace_with(target, writer=write)
    assert target.read_bytes() == b"new-content"


def test_failure_during_writer_does_not_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "out.bin"
    target.write_bytes(b"ORIGINAL")

    def boom(f: object) -> None:
        assert hasattr(f, "write")
        f.write(b"partial")  # type: ignore[attr-defined]
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError, match="disk full"):
        atomic_replace_with(target, writer=boom)

    # Original content must be preserved on failure.
    assert target.read_bytes() == b"ORIGINAL"


def test_writer_failure_cleans_up_tmp(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"

    def boom(f: object) -> None:
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        atomic_replace_with(target, writer=boom)

    leftovers = [
        p
        for p in tmp_path.iterdir()
        if p.name.startswith(".out.") and p.name.endswith(".tmp")
    ]
    assert leftovers == []


def stat_mode(path: Path) -> int:
    return stat_S_IMODE(os.stat(path).st_mode)


def stat_S_IMODE(mode: int) -> int:
    """Return the lower 12 bits of a stat mode (permission + setuid bits)."""
    return mode & 0o7777
