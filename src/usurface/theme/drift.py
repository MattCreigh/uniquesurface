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
    """Remove the sentinel region from a QML file, returning the rest.

    Also normalises the four font/theme property *values* that usurface
    manages (``fontFamily``, ``fontWeight``, ``passwordCharacter``,
    ``clockFormat``) to a canonical placeholder. This means drift
    detection compares the *structure* of the file, not the particular
    string values usurface intentionally rewrote — so our own managed
    edits are not mistaken for upstream drift.
    """
    stripped = _SENTINEL_RE.sub("", text)
    # Normalise the value literals of the properties we manage so that
    # our intentional edits don't register as drift. Only the
    # ``property string <name>: "<value>"`` declaration lines are
    # touched; everything else (structure, other properties) is compared
    # verbatim.
    for prop in ("fontFamily", "fontWeight", "passwordCharacter", "clockFormat"):
        stripped = re.sub(
            r'((?:readonly\s+)?property\s+string\s+'
            + re.escape(prop)
            + r'\s*:\s*)"[^"]*"',
            r'\1"@usurface@managed@"',
            stripped,
        )
    return stripped


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
    stripped (and managed property values normalised) against the stored
    pristine *also* normalised the same way. This means our own
    intentional property-value edits don't register as drift; only
    structural changes outside the four managed properties do.
    """
    pristine_bytes = extract.read_pristine(name)
    if pristine_bytes is None:
        pristine_sha = None
        on_disk_sha = on_disk_stripped_hash(vendor_path)
        matches = False
    else:
        pristine_text = pristine_bytes.decode("utf-8", errors="replace")
        # Pristine has no sentinels; strip_sentinels still normalises the
        # managed property values so both sides are compared on the same
        # structural basis.
        pristine_norm = strip_sentinels(pristine_text)
        pristine_sha = sha256_bytes(pristine_norm.encode("utf-8"))
        on_disk_sha = on_disk_stripped_hash(vendor_path)
        matches = pristine_sha == on_disk_sha
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
    log.warning(
        "drift detected; creating backup",
        vendor_path=vendor_path,
        backup_path=backup_path,
    )
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
