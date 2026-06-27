"""Atomic file writes (tmp + fsync + rename).

A power-cut mid-write must not leave a corrupted file. We write to a
sibling temporary file, fsync it, then ``os.replace`` over the target.
The destination directory must exist.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import IO


def atomic_write_bytes(
    path: os.PathLike[str] | str,
    data: bytes,
    *,
    mode: int | None = None,
) -> Path:
    """Write ``data`` to ``path`` atomically.

    Creates a sibling temp file, fsyncs it, then ``os.replace``s it over
    the target. If the target does not exist it is created. Parent
    directory must already exist.

    Parameters
    ----------
    path:
        Destination file path.
    data:
        Bytes to write.
    mode:
        Optional POSIX mode to apply to the result, e.g. ``0o644``.

    Returns
    -------
    pathlib.Path
        Resolved destination path.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{dest.name}.",
        suffix=".tmp",
        dir=str(dest.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, dest)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return dest


def atomic_write_text(
    path: os.PathLike[str] | str,
    text: str,
    *,
    mode: int | None = None,
    encoding: str = "utf-8",
) -> Path:
    """Text-mode convenience wrapper around :func:`atomic_write_bytes`."""
    return atomic_write_bytes(path, text.encode(encoding), mode=mode)


def atomic_replace_with(
    path: os.PathLike[str] | str,
    *,
    writer: Callable[[IO[bytes]], None],
    mode: int | None = None,
) -> Path:
    """Atomically replace ``path`` with content produced by ``writer``.

    ``writer`` receives an open binary file object positioned at offset 0;
    it must write the full desired content and may call ``flush`` /
    ``fsync`` itself, although we always fsync before rename anyway.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{dest.name}.",
        suffix=".tmp",
        dir=str(dest.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            writer(f)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, dest)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return dest
