"""Template drift detection.

For each tracked QML file we keep a pristine copy in the state dir.
On every patch we compare the SHA-256 of the on-disk file with the
sentinel region stripped against the stored pristine SHA-256.

If the hashes differ:
1. Save the current file as ``<path>.usurface.drift.<ts>``.
2. Re-extract a fresh pristine template from the running system.
3. If the re-extracted template still does not match the stripped
   on-disk file, emit a hard error and refuse to patch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from usurface.manifest import sha256_bytes, sha256_file
from usurface.theme import extract

_MARKER_START = "/* @usurface:start */"
_MARKER_END = "/* @usurface:end */"
_SENTINEL_RE = re.compile(
    re.escape(_MARKER_START) + r".*?" + re.escape(_MARKER_END),
    re.DOTALL,
)


@dataclass(frozen=True)
class DriftReport:
    """Result of a drift check."""

    name: str
    vendor_path: Path
    on_disk_matches_pristine: bool
    on_disk_matches_re_extracted: bool | None
    pristine_sha: str | None
    on_disk_stripped_sha: str | None
    drift_backup: Path | None


def strip_sentinels(text: str) -> str:
    """Remove the sentinel region from a QML file, returning the rest."""
    return _SENTINEL_RE.sub("", text)


def on_disk_stripped_hash(vendor_path: Path) -> str | None:
    """SHA-256 of the on-disk file with the usurface sentinel region removed."""
    if not vendor_path.is_file():
        return None
    text = vendor_path.read_text(encoding="utf-8", errors="replace")
    stripped = strip_sentinels(text).encode("utf-8")
    return sha256_bytes(stripped)


def check(
    name: str,
    vendor_path: Path,
) -> DriftReport:
    """Check whether ``vendor_path`` matches its stored pristine template.

    The comparison hashes the on-disk file with the sentinel region
    stripped against the stored pristine hash (also stripped). The
    function does NOT attempt to re-extract the pristine; that is the
    orchestrator's responsibility (``usurface qml-update-templates``).
    """
    pristine_sha = extract.pristine_sha256(name)
    on_disk_sha = on_disk_stripped_hash(vendor_path)
    matches = pristine_sha is not None and pristine_sha == on_disk_sha
    return DriftReport(
        name=name,
        vendor_path=vendor_path,
        on_disk_matches_pristine=matches,
        on_disk_matches_re_extracted=None,
        pristine_sha=pristine_sha,
        on_disk_stripped_sha=on_disk_sha,
        drift_backup=None,
    )


def handle_drift(name: str, vendor_path: Path) -> Path | None:
    """Check for drift. If detected:
    1. Save the current file as `<path>.usurface.drift.<ts>`.
    2. Re-extract a fresh pristine template from the running system (stripped of sentinels)
       and update the stored pristine.
    3. If they still don't match, raise RuntimeError.

    Returns the path to the backup file if drift was handled, or None.
    """
    report = check(name, vendor_path)
    if report.on_disk_matches_pristine:
        return None

    if not vendor_path.is_file():
        return None

    import shutil
    from usurface.logging import get_logger

    log = get_logger(__name__)

    # 1. Save backup
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = vendor_path.parent / f"{vendor_path.name}.usurface.drift.{ts}"
    log.warning("drift detected; creating backup", vendor_path=vendor_path, backup_path=backup_path)
    try:
        shutil.copy2(vendor_path, backup_path)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot create backup {backup_path}: permission denied. "
            "Please run as root/sudo to patch system files."
        ) from exc

    # 2. Re-extract pristine template (stripping sentinels first)
    text = vendor_path.read_text(encoding="utf-8", errors="replace")
    stripped_text = strip_sentinels(text)
    stripped_bytes = stripped_text.encode("utf-8")
    
    extract.copy_pristine_bytes(name, stripped_bytes)

    # 3. Check again
    new_report = check(name, vendor_path)
    if not new_report.on_disk_matches_pristine:
        raise RuntimeError(
            f"Drift check failed for '{name}' even after template re-extraction. "
            f"Pristine SHA: {new_report.pristine_sha}, Stripped SHA: {new_report.on_disk_stripped_sha}"
        )
    return backup_path


def on_disk_file_hash(path: Path) -> str | None:
    """Convenience wrapper used by tests."""
    return sha256_file(path)

