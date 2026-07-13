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


# --- Error / fallback paths ---


def test_fsync_dir_handles_oserror_on_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the directory can't be opened for fsync (some FUSE / container
    mounts), the function returns silently."""
    from trinity.atomic import _fsync_dir

    def raise_oserror(*_a: object, **_kw: object) -> int:
        raise OSError(errno.EACCES, "cannot open directory")

    monkeypatch.setattr(os, "open", raise_oserror)
    # Must not raise.
    _fsync_dir(tmp_path)


def test_fsync_dir_handles_oserror_on_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the directory fd is opened but fsync fails (some filesystems),
    the function swallows the error and closes the fd."""
    from trinity.atomic import _fsync_dir

    def fake_fsync(fd: int) -> None:
        raise OSError(errno.EIO, "fsync not supported")

    monkeypatch.setattr(os, "fsync", fake_fsync)
    _fsync_dir(tmp_path)
    # If we got here, the function handled the error and called close.


def test_fsync_dir_calls_close_even_on_fsync_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a failed fsync, the directory fd must still be closed."""
    from trinity.atomic import _fsync_dir

    closes: list[int] = []

    real_close = os.close
    monkeypatch.setattr(
        os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError(errno.EIO, "x"))
    )
    monkeypatch.setattr(
        os,
        "close",
        lambda fd: closes.append(fd) or real_close(fd),
    )
    _fsync_dir(tmp_path)
    assert closes, "close() was not called after a failed fsync"


def test_atomic_write_bytes_exdev_with_unwritable_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When EXDEV fires AND the parent directory is not writable for a
    sibling temp file, the writer falls back to a direct overwrite.

    The first ``mkstemp`` (in the platform temp dir) is allowed to
    succeed; only the sibling ``mkstemp`` in ``dest.parent`` raises.
    """
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")

    real_replace = os.replace
    calls = {"n": 0}

    def exdev_then_boom(src: object, dst: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", exdev_then_boom)

    # Only the second mkstemp (the sibling in dest.parent) should
    # raise; the first one (in the platform temp dir) must succeed.
    import tempfile as _tempfile

    real_mkstemp = _tempfile.mkstemp
    mkstemp_calls = {"n": 0}

    def mkstemp_guarded(*args: object, **kwargs: object) -> tuple[int, str]:
        mkstemp_calls["n"] += 1
        if mkstemp_calls["n"] == 2 and kwargs.get("dir", "") == str(tmp_path):
            raise PermissionError(errno.EACCES, "mkstemp: no write access")
        return real_mkstemp(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_tempfile, "mkstemp", mkstemp_guarded)

    atomic_write_bytes(target, b"new", mode=0o644)
    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o644


def test_atomic_write_bytes_direct_overwrite_raises_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the parent is unwritable AND the destination file itself is
    not writable, the direct-overwrite fallback raises a PermissionError
    with a helpful sudo hint."""
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")
    os.chmod(target, 0o400)  # read-only — copy will fail

    real_replace = os.replace
    calls = {"n": 0}

    def exdev_once(src: object, dst: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", exdev_once)

    import tempfile as _tempfile

    real_mkstemp = _tempfile.mkstemp
    mkstemp_calls = {"n": 0}

    def mkstemp_guarded(*args: object, **kwargs: object) -> tuple[int, str]:
        mkstemp_calls["n"] += 1
        if mkstemp_calls["n"] == 2 and kwargs.get("dir", "") == str(tmp_path):
            raise PermissionError(errno.EACCES, "no write")
        return real_mkstemp(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_tempfile, "mkstemp", mkstemp_guarded)

    with pytest.raises(PermissionError, match=r"Permission denied"):
        atomic_write_bytes(target, b"new")


def test_atomic_write_text_exception_cleans_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error in atomic_write_text also cleans up its temp file."""
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    def boom(src: object, dst: object) -> None:
        raise OSError(errno.EIO, "fail")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(tmp_path / "out.txt", "data", mode=0o644)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert leftovers == [], f"temp files leaked: {leftovers}"


def test_atomic_replace_with_exception_cleans_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception in the writer callback or in _atomic_move removes
    the temp file."""
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    def boom(src: object, dst: object) -> None:
        raise OSError(errno.EIO, "fail")

    monkeypatch.setattr(os, "replace", boom)

    def bad_writer(f) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("writer crashed")

    with pytest.raises(RuntimeError, match="writer crashed"):
        atomic_replace_with(tmp_path / "out.bin", writer=bad_writer)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert leftovers == [], f"temp files leaked: {leftovers}"


def test_atomic_write_bytes_with_exdev_and_existing_file_no_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EXDEV fallback + no mode passed → existing file mode is kept."""
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")
    os.chmod(target, 0o600)
    real_replace = os.replace
    calls = {"n": 0}

    def exdev_once(src: object, dst: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", exdev_once)
    atomic_write_bytes(target, b"new")  # no mode
    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
