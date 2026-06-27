"""Copy pristine vendor QML files into the per-user state directory.

The state directory at ``~/.local/state/usurface/templates/`` is the
authoritative source of pristine QML for drift detection and the first
patch. Files are copied with their original content unchanged so the
hashes match what was on disk before usurface touched anything.

This module is intentionally not auto-invoked; the orchestrator calls
it from ``usurface install`` and ``usurface qml-update-templates``.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

from usurface import paths
from usurface.manifest import sha256_bytes

# (logical_name, vendor_path)
DEFAULT_TARGETS: list[tuple[str, Path]] = [
    ("sddm_login", Path("/usr/share/sddm/themes/breeze/Login.qml")),
    (
        "plasma_lockscreen_mainblock",
        Path("/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/MainBlock.qml"),
    ),
    (
        "plasma_lockscreen_ui",
        Path("/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/LockScreenUi.qml"),
    ),
]


def extract(targets: Iterable[tuple[str, Path]] | None = None) -> list[Path]:
    """Copy each target vendor file into the state-dir templates/.

    Returns the list of destination paths actually written (skipped if
    the vendor file does not exist).
    """
    out: list[Path] = []
    dest_dir = paths.templates_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name, src in targets or DEFAULT_TARGETS:
        if not src.is_file():
            continue
        dest = dest_dir / f"{name}.qml"
        data = src.read_bytes()
        dest.write_bytes(data)
        out.append(dest)
    return out


def copy_pristine(name: str, vendor_path: Path) -> Path:
    """Copy a single vendor file into ``templates/<name>.qml``."""
    dest = paths.templates_dir() / f"{name}.qml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(vendor_path, dest)
    return dest


def copy_pristine_bytes(name: str, data: bytes) -> Path:
    """Write ``data`` into ``templates/<name>.qml`` as the new pristine."""
    dest = paths.templates_dir() / f"{name}.qml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def read_pristine(name: str) -> bytes | None:
    """Return the bytes of ``templates/<name>.qml`` or ``None`` if absent."""
    p = paths.templates_dir() / f"{name}.qml"
    if not p.is_file():
        return None
    return p.read_bytes()


def pristine_sha256(name: str) -> str | None:
    """Return the SHA-256 of the stored pristine template, or ``None``."""
    data = read_pristine(name)
    if data is None:
        return None
    return sha256_bytes(data)
