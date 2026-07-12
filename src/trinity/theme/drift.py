"""Template drift detection.

For each tracked QML file we keep a pristine copy in the state dir.
On every patch we compare the SHA-256 of the on-disk file (sentinel
region stripped, managed values normalised) against the stored
pristine hashed the same way.

If the hashes differ:
1. Save the current file as ``<path>.trinity.drift.<ts>`` (unless an
   identical backup already exists).
2. Raise :class:`DriftError` and refuse to patch. The drifted content
   is never silently adopted; the user consents explicitly via
   ``trinity qml-update-templates`` or ``trinity apply --adopt-drift``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from trinity.manifest import sha256_bytes, sha256_file
from trinity.theme import extract
from trinity.theme.qml_patch import (
    _FADEOUT_TIMER_INTERVAL_RE,
    _WAKE_GUARD_BLOCK_RE,
    HEADER_LINE,
)

if TYPE_CHECKING:
    from trinity.theme.descriptors import PlasmaVersion

_MARKER_START = "/* @trinity:start */"
_MARKER_END = "/* @trinity:end */"
# Match the optional ``// managed by trinity`` header line plus the
# sentinel block, so the normaliser's output is byte-equal to the
# stored pristine which never had the header. Without this, a
# re-patched file would always report drift after every apply.
# The trailing ``\n?`` swallows the newline after ``SENTINEL_END``
# because :func:`trinity.theme.qml_patch._ensure_sentinels` always
# appends a ``\n`` after the footer — leaving one extra trailing
# newline compared to the pristine if not consumed.
_SENTINEL_RE = re.compile(
    r"(?:"
    + re.escape(HEADER_LINE)
    + r"[^\n]*\n"
    + r")?"
    + re.escape(_MARKER_START)
    + r".*?"
    + re.escape(_MARKER_END)
    + r"\n?",
    re.DOTALL,
)


@dataclass(frozen=True)
class DriftReport:
    """Result of a drift check."""

    name: str
    vendor_path: Path
    on_disk_matches_pristine: bool
    pristine_sha: str | None
    on_disk_stripped_sha: str | None
    drift_backup: Path | None


def strip_sentinels(text: str) -> str:
    """Remove the trinity sentinel region from a QML file, returning the rest.

    Used in two distinct contexts:

    1. **Pristine extraction** (see :func:`trinity.theme.extract.extract`)
       — we want the *un-patched vendor content* to be the stored
       baseline, so we strip the sentinel block (and only the sentinel
       block) before writing the file into ``templates/``.

    2. **Drift detection on the patched file** — we want to ignore the
       sentinel block when comparing the on-disk file to the stored
       pristine. The sentinel markers are our own annotations and
       would otherwise register as drift.

    The value-literal normalisations (managed font/theme properties,
    the ``fadeoutTimer`` interval, the wake-keypress guard) live in
    :func:`normalize_managed_values` and are applied separately, on
    demand, *only* when computing the drift hash. Applying them during
    pristine extraction would corrupt the stored baseline.
    """
    return _SENTINEL_RE.sub("", text)


# Property names whose values trinity rewrites intentionally. Listed as
# a module-level constant so the normaliser and the apply path share
# the same set.
#
# As of Phase 4, the *authoritative* source is the descriptor TOML for
# the target file.  This tuple is a fallback for the case where no
# descriptor matches the current Plasma version (CI, containerised
# runs, no ``plasmashell`` on PATH).
_MANAGED_PROPS_FALLBACK: tuple[str, ...] = (
    "fontFamily",
    "fontWeight",
    "passwordCharacter",
    "clockFormat",
)


def _managed_props_for(name: str) -> tuple[str, ...]:
    """Return the set of font/theme property names trinity manages for
    ``name``, sourced from the matching descriptor when one is found.

    Falls back to :data:`_MANAGED_PROPS_FALLBACK` when the descriptor
    system has no match for the current Plasma version.
    """
    from trinity.theme.descriptors import _all as _all_descriptors
    from trinity.theme.descriptors import _specifier_matches

    plasma = _detect_plasma_version_cached()
    version = plasma.version
    if version is not None:
        for d in _all_descriptors():
            if d.name != name:
                continue
            if not _specifier_matches(d, version):
                continue
            props: list[str] = []
            for p in d.patches:
                if p.kind != "font_property":
                    continue
                for fp in p.font_properties:
                    props.append(fp.name)
            if props:
                return tuple(props)
    return _MANAGED_PROPS_FALLBACK


def _detect_plasma_version_cached() -> PlasmaVersion:
    """Cache the Plasma version per process to avoid re-running
    ``plasmashell --version`` for every managed property lookup."""
    global _PLASMA_CACHE
    if _PLASMA_CACHE is None:
        from trinity.theme.descriptors import detect_plasma_version

        _PLASMA_CACHE = detect_plasma_version()
    # ``_PLASMA_CACHE`` is typed ``Any`` because it's set lazily; the
    # branch above guarantees it's a ``PlasmaVersion`` here.
    assert _PLASMA_CACHE is not None
    result: PlasmaVersion = _PLASMA_CACHE
    return result


_PLASMA_CACHE: Any = None  # PlasmaVersion | None; lazily populated


def normalize_managed_values(text: str, *, target_name: str | None = None) -> str:
    """Normalise the values trinity intentionally rewrites to a placeholder.

    Applies three normalisations, in order:

    1. The four font/theme property value literals (``fontFamily``,
       ``fontWeight``, ``passwordCharacter``, ``clockFormat``) are
       replaced with ``"@trinity@managed@"``.
    2. The ``fadeoutTimer`` interval number is replaced with
       ``@trinity@managed@`` so a config change of
       ``on_idle_dim_seconds`` does not register as drift.
    3. The wake-keypress guard (inserted into the password box's
       ``Keys.onPressed`` handler in ``MainBlock.qml``) is removed.

    Used only when computing the drift hash, so a vanilla
    ``extract()`` round-trip does not corrupt the stored pristine
    baseline with placeholder text.

    ``target_name`` is the logical name of the vendor file being
    normalised (e.g. ``"sddm_login"``).  When provided, the set of
    managed property names is sourced from the matching descriptor;
    otherwise the fallback list is used.
    """
    if target_name is None:
        props = _MANAGED_PROPS_FALLBACK
    else:
        props = _managed_props_for(target_name)
    for prop in props:
        text = re.sub(
            r"((?:readonly\s+)?property\s+string\s+"
            + re.escape(prop)
            + r'\s*:\s*)"[^"]*"',
            r'\1"@trinity@managed@"',
            text,
        )
    text = _FADEOUT_TIMER_INTERVAL_RE.sub(r"\g<1>@trinity@managed@", text)
    text = _WAKE_GUARD_BLOCK_RE.sub("", text)
    return text


def on_disk_stripped_hash(vendor_path: Path, *, name: str | None = None) -> str | None:
    """SHA-256 of the on-disk file with sentinels stripped and managed
    values normalised, ready for comparison against the (also normalised)
    stored pristine.

    ``name`` is the logical target name (e.g. ``"sddm_login"``); when
    provided, the descriptor for the current Plasma version determines
    the set of managed properties.  When omitted, the fallback list
    is used (suitable for tests that bypass the descriptor system).
    """
    if not vendor_path.is_file():
        return None
    text = vendor_path.read_text(encoding="utf-8", errors="replace")
    stripped = normalize_managed_values(strip_sentinels(text), target_name=name).encode(
        "utf-8"
    )
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
        on_disk_sha = on_disk_stripped_hash(vendor_path, name=name)
        matches = False
    else:
        pristine_text = pristine_bytes.decode("utf-8", errors="replace")
        # Pristine has no sentinels; apply the same value normalisation
        # to both sides so the comparison is structural, not value-based.
        pristine_norm = normalize_managed_values(pristine_text, target_name=name)
        pristine_sha = sha256_bytes(pristine_norm.encode("utf-8"))
        on_disk_sha = on_disk_stripped_hash(vendor_path, name=name)
        matches = pristine_sha == on_disk_sha
    return DriftReport(
        name=name,
        vendor_path=vendor_path,
        on_disk_matches_pristine=matches,
        pristine_sha=pristine_sha,
        on_disk_stripped_sha=on_disk_sha,
        drift_backup=None,
    )


class DriftError(RuntimeError):
    """Raised when a vendor QML file has drifted from the stored pristine.

    The drifted content is NOT automatically adopted as the new pristine
    baseline — the user must explicitly consent by running
    ``trinity qml-update-templates`` (or ``trinity apply --adopt-drift``).
    The error message names the file, both SHAs, the backup path, and the
    remediation command.
    """

    def __init__(
        self,
        *,
        name: str,
        vendor_path: Path,
        backup_path: Path,
        pristine_sha: str | None,
        on_disk_sha: str | None,
    ) -> None:
        self.name = name
        self.vendor_path = vendor_path
        self.backup_path = backup_path
        self.pristine_sha = pristine_sha
        self.on_disk_sha = on_disk_sha
        super().__init__(
            f"QML drift detected for '{name}' ({vendor_path}).\n"
            f"  pristine sha : {pristine_sha or 'missing'}\n"
            f"  on-disk sha  : {on_disk_sha or 'missing'}\n"
            f"  backup       : {backup_path}\n"
            f"The drifted content was NOT adopted as the new baseline. "
            f"To accept the new vendor content, run "
            f"`trinity qml-update-templates` (or `trinity apply --adopt-drift`)."
        )


def handle_drift(name: str, vendor_path: Path) -> Path | None:
    """Check for drift. If detected:
    1. Save the current file as `<path>.trinity.drift.<ts>`.
    2. Log a prominent warning naming the file and backup.
    3. Raise :class:`DriftError` instructing the user to run
       ``trinity qml-update-templates`` (or ``trinity apply --adopt-drift``)
       to explicitly accept the new vendor content.

    The drifted content is NEVER silently adopted as the new pristine
    baseline — that would let any third-party (or hostile) modification
    become the trusted baseline without consent.

    Returns the path to the backup file if drift was handled, or None.
    """
    report = check(name, vendor_path)
    if report.on_disk_matches_pristine:
        return None

    if not vendor_path.is_file():
        return None

    from trinity.logging_setup import get_logger

    log = get_logger(__name__)

    # If there is no stored pristine baseline, this is NOT drift — it is
    # "trinity install has never been run". We still raise DriftError so
    # the orchestrator skips patching, but we do NOT create a timestamped
    # backup file (which would accumulate in the vendor directory on every
    # apply until the user runs install). The message guides the user to
    # install first.
    if report.pristine_sha is None:
        log.warning(
            "no_pristine_template",
            name=name,
            vendor_path=str(vendor_path),
            hint="run 'trinity install' first",
        )
        raise DriftError(
            name=name,
            vendor_path=vendor_path,
            backup_path=vendor_path,  # no backup created; point at the file
            pristine_sha=None,
            on_disk_sha=report.on_disk_stripped_sha,
        )

    import shutil

    # 1. Save backup — unless an earlier run already backed up this exact
    #    content. Under the daily timer, unresolved drift would otherwise
    #    create one timestamped backup per apply, accumulating forever in
    #    the vendor directory.
    current_sha = sha256_file(vendor_path)
    existing = _existing_backup(vendor_path, current_sha)
    if existing is not None:
        log.warning(
            "qml_drift_backup_exists",
            vendor_path=str(vendor_path),
            backup_path=str(existing),
        )
        backup_path = existing
    else:
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup_path = vendor_path.parent / f"{vendor_path.name}.trinity.drift.{ts}"
        log.warning(
            "qml_drift_backup_created",
            vendor_path=str(vendor_path),
            backup_path=str(backup_path),
        )
        try:
            shutil.copy2(vendor_path, backup_path)
        except PermissionError as exc:
            raise PermissionError(
                f"Cannot create backup {backup_path}: permission denied. "
                "Please run as root/sudo to patch system files."
            ) from exc
        # Retention: the content-dedupe above only prevents duplicates of
        # the *same* drifted content. During active iteration the vendor
        # file changes between applies, which once littered a system
        # directory with 100+ timestamped backups. Keep the newest few.
        for removed in _prune_old_backups(vendor_path):
            log.warning(
                "qml_drift_backup_pruned",
                vendor_path=str(vendor_path),
                backup_path=str(removed),
            )

    # 2. Refuse to adopt the drifted content as pristine. Raise so the
    #    orchestrator can skip this file and report the remediation.
    raise DriftError(
        name=name,
        vendor_path=vendor_path,
        backup_path=backup_path,
        pristine_sha=report.pristine_sha,
        on_disk_sha=report.on_disk_stripped_sha,
    )


def _existing_backup(vendor_path: Path, content_sha: str | None) -> Path | None:
    """Return an existing drift backup of ``vendor_path`` whose content
    matches ``content_sha``, or None if there is none."""
    if content_sha is None:
        return None
    for candidate in sorted(
        vendor_path.parent.glob(f"{vendor_path.name}.trinity.drift.*")
    ):
        if sha256_file(candidate) == content_sha:
            return candidate
    return None


# How many drift backups to retain per vendor file. The newest backup is
# always the one the current DriftError message points at, so it is never
# pruned; two older generations give enough forensic history without
# letting a dev loop fill the vendor directory.
_MAX_DRIFT_BACKUPS = 3


def _prune_old_backups(
    vendor_path: Path, *, keep: int = _MAX_DRIFT_BACKUPS
) -> list[Path]:
    """Remove all but the ``keep`` newest drift backups of ``vendor_path``.

    The ``YYYYMMDD_HHMMSS`` timestamp suffix sorts lexicographically in
    chronological order. Removal failures are skipped — retention is
    best-effort and must never turn a drift report into a crash.
    """
    backups = sorted(vendor_path.parent.glob(f"{vendor_path.name}.trinity.drift.*"))
    removed: list[Path] = []
    for old in backups[:-keep] if keep > 0 else backups:
        try:
            old.unlink()
            removed.append(old)
        except OSError:
            continue
    return removed
