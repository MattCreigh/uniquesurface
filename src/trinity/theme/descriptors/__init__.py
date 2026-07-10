"""QML patch descriptor loader.

A descriptor is a TOML file under ``descriptors/`` that declares:

* which QML file it targets (``name``);
* which Plasma versions it covers (``plasma``);
* the managed patches (font properties, fadeout timer, wake guard).

The loader validates each descriptor against the pydantic models in
:mod:`.schema`.  A malformed packaged descriptor is a *bug* and fails
loudly at first load — we never silently fall back to hard-coded
defaults, because the whole point of the descriptor system is to
externalise the anchors so they can be updated without a code change.

Selection
=========

:func:`select` picks the best descriptor for a given ``name`` and
detected Plasma version.  Rules:

1. ``name`` must match.
2. ``plasma`` PEP 440 range must contain the version (or be empty,
   which means "any").
3. Version must be in ``include`` (if non-empty) and not in
   ``exclude``.
4. If multiple descriptors match, the most specific wins: longer
   ``plasma`` string first, then a non-empty ``plasma`` over an
   empty one, then alphabetical ``name`` for determinism.

If no descriptor matches, :func:`select` returns ``None``; the apply
path treats this as "theme tokens unsupported on Plasma X.Y" and
records the structured status.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from trinity.theme.descriptors._schema import QmlDescriptor

_DESCRIPTORS_DIR = Path(__file__).parent

# All loaded descriptors, in file-name order.  Computed once on first
# import and cached; tests that need a different set should use
# :func:`load_descriptors_from_dir` directly with a tmp dir.
_cached: list[QmlDescriptor] | None = None


@dataclass(frozen=True)
class PlasmaVersion:
    """Detected Plasma version, defensively parsed.

    ``source`` is one of:

    * ``"binary"`` — parsed from ``plasmashell --version`` output.
    * ``"config"`` — user-supplied via env / config.
    * ``"unknown"`` — neither available; ``version_str`` is empty.

    We never raise from a parse failure: an unparseable string
    produces ``unknown`` and the loader treats that as "no
    descriptor matches" (graceful skip, not crash).
    """

    version_str: str
    source: str

    @property
    def version(self) -> Version | None:
        """Parsed :class:`packaging.version.Version`, or ``None``."""
        if not self.version_str:
            return None
        try:
            return Version(self.version_str)
        except InvalidVersion:
            return None

    @property
    def known(self) -> bool:
        return self.version is not None


def detect_plasma_version() -> PlasmaVersion:
    """Detect the running Plasma version, defensively.

    Resolution order:

    1. ``$TRINITY_PLASMA_VERSION`` if set and parseable.
    2. ``plasmashell --version`` if on PATH and exits within 2 s.
    3. ``unknown`` — the apply path will treat no matching descriptor
       as "skip with status" rather than crash.

    The function is intentionally best-effort: a missing plasmashell
    on a CI runner, a sandboxed container, or an SDDM-only host are
    all legitimate environments where the version is unknown.
    """
    import os
    import shutil
    import subprocess

    forced = os.environ.get("TRINITY_PLASMA_VERSION", "").strip()
    if forced:
        try:
            Version(forced)
        except InvalidVersion:
            pass
        else:
            return PlasmaVersion(forced, "config")

    binary = shutil.which("plasmashell")
    if binary is None:
        return PlasmaVersion("", "unknown")

    try:
        proc = subprocess.run(
            [binary, "--version"],  # argv list, never shell=True
            timeout=2.0,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return PlasmaVersion("", "unknown")

    raw = (proc.stdout or "") + "\n" + (proc.stderr or "")
    parsed = _parse_plasma_version_output(raw)
    if parsed:
        return PlasmaVersion(parsed, "binary")
    return PlasmaVersion("", "unknown")


def _parse_plasma_version_output(raw: str) -> str:
    """Pull a `N.N` or `N.N.N` style version from plasmashell output.

    plasmashell's ``--version`` output is locale-dependent and
    not stable across releases; we look for the first
    ``<digit>(.<digit>)+`` token.  This is intentionally permissive
    — the PEP 440 parser will reject garbage, and a parse failure
    produces ``unknown`` which is the same as a missing binary.
    """
    import re

    m = re.search(r"\b(\d+(?:\.\d+){1,2})\b", raw)
    return m.group(1) if m else ""


def _load_all() -> list[QmlDescriptor]:
    """Read every ``.toml`` under the descriptors dir and validate.

    Order is file-name order for determinism.  Any descriptor that
    fails to load or validate raises immediately — a malformed
    packaged descriptor is a bug.
    """
    out: list[QmlDescriptor] = []
    for path in sorted(_DESCRIPTORS_DIR.glob("*.toml")):
        out.extend(load_descriptors_from_dir(path.parent, [path.name]))
    return out


def load_descriptors_from_dir(
    directory: Path, filenames: list[str] | None = None
) -> list[QmlDescriptor]:
    """Load and validate descriptors from ``directory``.

    ``filenames`` is the list of TOML file names to read; ``None``
    means "every ``*.toml`` in the directory" (in sorted order).
    This entry point is what tests use to load a custom set under
    ``tmp_path``.
    """
    if filenames is None:
        filenames = sorted(p.name for p in directory.glob("*.toml"))
    out: list[QmlDescriptor] = []
    for name in filenames:
        path = directory / name
        with path.open("rb") as f:
            data: dict[str, Any] = tomllib.load(f)
        descriptor = QmlDescriptor.model_validate(data)
        descriptor.post_validate()
        out.append(descriptor)
    return out


def _all() -> list[QmlDescriptor]:
    """All packaged descriptors, computed once and cached."""
    global _cached
    if _cached is None:
        _cached = _load_all()
    return _cached


def _reset_cache_for_tests() -> None:
    """Drop the descriptor cache.  Tests that mutate the package
    descriptors directory use this between fixtures."""
    global _cached
    _cached = None


def _specifier_matches(descriptor: QmlDescriptor, version: Version) -> bool:
    """Apply the include/exclude filters and the ``plasma`` specifier.

    An empty ``plasma`` string is treated as "any version" (wildcard
    fallback).  ``include`` and ``exclude`` are exact-version lists;
    PEP 440 pre-releases (``6.0.0rc1``) are normalised so equality
    checks work.
    """
    if descriptor.plasma:
        try:
            spec = SpecifierSet(descriptor.plasma, prereleases=True)
        except InvalidSpecifier:
            return False
        if version not in spec:
            return False
    if descriptor.include and str(version) not in descriptor.include:
        return False
    if str(version) in descriptor.exclude:
        return False
    return True


def _specificity_key(d: QmlDescriptor) -> tuple[int, int, int, int, str]:
    """Sort key for selecting the *most specific* matching descriptor.

    Higher numbers = more specific.  Rank by:

    1. presence of a non-empty specifier (1 if present, 0 if empty);
    2. count of ``include`` entries — more entries = more specific
       (overrides and patches that pin a known set of versions
       take priority over broad ranges);
    3. count of ``exclude`` entries — a descriptor that *narrows*
       its range with excludes is more specific than one that
       doesn't;
    4. *negative* count of commas in the specifier — fewer
       clauses is more specific;
    5. alphabetical ``name`` for full determinism.
    """
    has_spec = 1 if d.plasma else 0
    return (
        has_spec,
        len(d.include),
        len(d.exclude),
        -d.plasma.count(","),
        d.name,
    )


def select(
    name: str,
    plasma: PlasmaVersion,
) -> QmlDescriptor | None:
    """Pick the best descriptor for ``name`` matching ``plasma``.

    Returns ``None`` if no descriptor matches; the caller treats
    that as "skip with structured status".
    """
    version = plasma.version
    if version is None:
        return None
    candidates: list[QmlDescriptor] = []
    for d in _all():
        if d.name != name:
            continue
        if _specifier_matches(d, version):
            candidates.append(d)
    if not candidates:
        return None
    candidates.sort(key=_specificity_key, reverse=True)
    return candidates[0]


__all__ = [
    "PlasmaVersion",
    "detect_plasma_version",
    "load_descriptors_from_dir",
    "select",
]
