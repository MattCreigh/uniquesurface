"""Atomic file writes (tmp + fsync + rename).

A power-cut mid-write must not leave a corrupted file. We write to a
temporary file, fsync it, then ``os.replace`` over the target.

The temp file is created in the platform temp directory so that a
user-mode run can still update a destination whose parent directory is
owned by another user (for example, after an earlier ``sudo`` run left
files behind). If ``os.replace`` fails because the temp directory and the
destination live on different filesystems, we fall back to a sibling temp
file in the destination directory. If that is also unwritable we fall
back to a direct overwrite so the user gets a clear permission error on
the destination path rather than on an internal temp file.
"""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import IO


def _atomic_move(tmp_path: Path, dest: Path, mode: int | None = None) -> None:
    """Move ``tmp_path`` onto ``dest`` atomically if possible.

    The primary temp directory is the platform temp directory. On most
    Linux systems ``/tmp`` is a ``tmpfs`` mount, while ``/home`` lives on
    the root filesystem, so ``os.replace`` raises ``EXDEV``. We then try a
    sibling temp file in ``dest.parent``. If that directory is not
    writable (e.g. root-owned after a previous ``sudo`` run), we fall back
    to writing directly to ``dest``. In that case atomicity is lost but
    the user sees a permission error pointing at the real destination path
    instead of an internal temp file, and the operation still succeeds
    if the destination file itself is writable.

    If ``mode`` is given, it is applied to the destination *after* the
    move, because ``os.replace`` preserves the mode of the pre-existing
    file (or uses the process umask for new files), ignoring the mode of
    the temp file.
    """
    try:
        os.replace(tmp_path, dest)
        if mode is not None:
            os.chmod(dest, mode)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        sibling_fd, sibling_name = tempfile.mkstemp(
            prefix=f".{dest.name}.",
            suffix=".tmp",
            dir=str(dest.parent),
        )
    except PermissionError:
        # Parent directory is not writable. Try a direct overwrite of the
        # destination file, which may still work if the file itself is
        # writable even though new files cannot be created in the dir.
        _direct_overwrite(tmp_path, dest)
        if mode is not None:
            os.chmod(dest, mode)
        return

    sibling_tmp = Path(sibling_name)
    try:
        with os.fdopen(sibling_fd, "wb") as f:
            with tmp_path.open("rb") as src:
                f.write(src.read())
            f.flush()
            os.fsync(f.fileno())
        os.replace(sibling_tmp, dest)
        if mode is not None:
            os.chmod(dest, mode)
    finally:
        try:
            sibling_tmp.unlink()
        except FileNotFoundError:
            pass
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _direct_overwrite(src: Path, dest: Path) -> None:
    """Copy ``src`` over ``dest`` as a last-resort, non-atomic fallback."""
    try:
        shutil.copy2(str(src), str(dest))
    except OSError:
        # If copy fails because dest is not writable, re-raise with a
        # helpful message naming the real destination path.
        raise PermissionError(
            errno.EACCES,
            f"Permission denied writing to {dest}. "
            f"If you previously ran trinity with sudo, run: "
            f"sudo chown -R $USER:$USER {dest.parent}",
        ) from None
    finally:
        try:
            src.unlink()
        except FileNotFoundError:
            pass


def atomic_write_bytes(
    path: os.PathLike[str] | str,
    data: bytes,
    *,
    mode: int | None = None,
) -> Path:
    """Write ``data`` to ``path`` atomically.

    Creates a temp file, fsyncs it, then ``os.replace``s it over the
    target. Parent directory must already exist (it is created if not).

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
        dir=tempfile.gettempdir(),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        _atomic_move(tmp_path, dest, mode=mode)
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
        dir=tempfile.gettempdir(),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            writer(f)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        _atomic_move(tmp_path, dest, mode=mode)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return dest
