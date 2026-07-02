"""QML patching for login + lock screens.

Strategy: replace the *values* of existing ``readonly property string``
font/theme-token declarations inside the vendor QML files. Plasma's own
lockscreen and SDDM Breeze files already declare:

    readonly property string fontFamily: "DejaVu Sans"
    readonly property string fontWeight: "Normal"
    readonly property string passwordCharacter: "*"
    readonly property string clockFormat: "hh:mm"

usurface rewrites the string literals in place. This keeps the QML a
valid single-root document (the previous approach appended a
``pragma Singleton`` block, which is a syntax error in a non-singleton
file and caused ``kscreenlocker_greet`` to fall back to the built-in
blue locker).

For drift detection and restore we still record the changed region
between sentinel *comments* (which are valid QML), so the file remains
parseable while we can still locate and revert our edits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from usurface.manifest import Manifest, sha256_bytes, write_tracked
from usurface.theme import extract

# Sentinel markers; anything between them is replaced wholesale.
SENTINEL_START = "/* @usurface:start */"
SENTINEL_END = "/* @usurface:end */"
_HEADER = f"// managed by usurface — do not edit\n{SENTINEL_START}\n"
_FOOTER = f"\n{SENTINEL_END}\n"


@dataclass(frozen=True)
class FontPatch:
    family: str
    weight: str
    password_character: str
    clock_format: str


@dataclass(frozen=True)
class LockPatch:
    """Lock-screen tokens applied as structural QML edits.

    ``on_idle_dim_seconds`` rewrites the ``fadeoutTimer`` interval
    (seconds → milliseconds). ``suppress_wake_keypress`` is currently
    a documented no-op (see A4 fallback in docs/config-reference.md).
    """

    on_idle_dim_seconds: int
    suppress_wake_keypress: bool


def _ensure_sentinels(text: str, block: str) -> str:
    """Ensure ``text`` contains the sentinel region with ``block`` inside.

    If the sentinel region already exists, replace its body with ``block``
    while preserving the ``// managed by usurface`` header comment that
    precedes the start marker. Otherwise append the region as a new
    comment block. The region is valid QML (a comment wrapping plain
    assignment statements) so the file remains parseable.
    """
    if SENTINEL_START in text and SENTINEL_END in text:
        pattern = re.compile(
            re.escape(SENTINEL_START) + r".*?" + re.escape(SENTINEL_END),
            re.DOTALL,
        )
        # Mirror the append format (_HEADER ends with \n, _FOOTER starts
        # with \n) so a re-patch with identical content is a true no-op.
        return pattern.sub(
            f"{SENTINEL_START}\n{block}\n{SENTINEL_END}", text
        )

    sep = "" if text.endswith("\n") else "\n"
    return f"{text}{sep}{_HEADER}{block}{_FOOTER}"


def _file_with_sentinels(text: str) -> bool:
    return SENTINEL_START in text and SENTINEL_END in text


def apply_font_tokens(
    *,
    name: str,
    vendor_path: Path,
    manifest: Manifest,
    patch: FontPatch,
    require_sentinels: bool = False,
) -> str:
    """Apply ``patch`` by rewriting the values of existing font/theme
    ``readonly property string`` declarations inside ``vendor_path``.

    The vendor QML files may declare properties such as::

        readonly property string fontFamily: "DejaVu Sans"

    We rewrite the string literal on each such line to the configured
    value. If the file declares *none* of the managed properties this is
    a no-op — we never append a block, since adding declarations or a
    ``pragma Singleton`` to a non-singleton QML file is a syntax error
    that breaks ``kscreenlocker_greet``.

    A sentinel *comment* region is appended only when at least one
    property was changed, to record that the file is managed by usurface
    and to support drift detection / restore; it contains only comments
    so the QML stays parseable.

    Drift detection is performed by :func:`drift.check` and should be
    invoked separately by the orchestrator before each patch; this
    function trusts the caller to have done so.

    Returns a one-line description of the action taken.
    """
    if not vendor_path.exists():
        raise FileNotFoundError(vendor_path)

    pristine = extract.read_pristine(name)
    if pristine is None:
        raise RuntimeError(
            f"no pristine template stored for {name}; run 'usurface install' first"
        )

    text = vendor_path.read_text(encoding="utf-8", errors="replace")

    # Rewrite the value of each managed property declaration in place.
    new_text, replaced_count = _replace_property_values(text, patch)

    # If the file declares none of the managed properties, do nothing.
    # Appending a block to a QML file that doesn't expect it is a syntax
    # error (this was the root cause of the blue lock screen).
    if replaced_count == 0 and not _file_with_sentinels(text):
        return f"{name}: no managed properties present; skipped"

    has_sentinels = _file_with_sentinels(new_text)
    marker_block = f"// fontFamily={patch.family} fontWeight={patch.weight}\n"
    if not has_sentinels:
        if require_sentinels:
            raise RuntimeError(
                f"{vendor_path} does not contain sentinels and require_sentinels=True"
            )
        new_text = _ensure_sentinels(new_text, marker_block)
    else:
        new_text = _ensure_sentinels(new_text, marker_block)

    if new_text == text:
        return f"{name}: no change"

    new_bytes = new_text.encode("utf-8")
    write_tracked(manifest, vendor_path, new_bytes, mode=0o644)

    return f"{name}: wrote {len(new_bytes)} bytes (sha {sha256_bytes(new_bytes)[:12]}…)"


def _replace_property_values(
    text: str, patch: "FontPatch"
) -> tuple[str, int]:
    """Replace the string literal in each managed ``readonly property
    string <name>: "<old>"`` declaration with the patched value.

    Matches common declaration variants:
        readonly property string fontFamily: "DejaVu Sans"
        property string fontFamily: "DejaVu Sans"
    The matcher is anchored on the property name so unrelated lines are
    untouched. If a declaration is absent the text is left unchanged for
    that property (the file may simply not declare it).

    Returns ``(new_text, count)`` where ``count`` is the number of
    property values actually replaced. A count of 0 means the file
    declares none of the managed properties.
    """
    values = {
        "fontFamily": patch.family,
        "fontWeight": patch.weight,
        "passwordCharacter": patch.password_character,
        "clockFormat": patch.clock_format,
    }
    out = text
    count = 0
    for prop, value in values.items():
        # Replace the value literal of `... property string <prop>: "<old>"`.
        # Allow optional `readonly`. Capture the literal in group 1.
        pattern = re.compile(
            r'((?:readonly\s+)?property\s+string\s+'
            + re.escape(prop)
            + r'\s*:\s*)"[^"]*"'
        )
        replacement = value.replace("\\", "\\\\").replace('"', '\\"')
        new_out, n = pattern.subn(rf'\1"{replacement}"', out, count=1)
        if n:
            out = new_out
            count += n
    return out, count


def remove_sentinels(*, name: str, vendor_path: Path, manifest: Manifest) -> str:
    """Strip the sentinel region and restore the file to pristine content."""
    pristine = extract.read_pristine(name)
    if pristine is None:
        raise RuntimeError(f"no pristine template stored for {name}")
    if not vendor_path.exists():
        raise FileNotFoundError(vendor_path)

    write_tracked(manifest, vendor_path, pristine, mode=0o644)
    return f"{name}: restored to pristine"


# --- lock-screen structural patching (A3: fadeoutTimer interval) ---

# The fadeoutTimer in LockScreenUi.qml controls how long the lock screen
# stays visible before dimming. Its interval is a literal in milliseconds.
_FADEOUT_TIMER_INTERVAL_RE = re.compile(
    r"(Timer\s*\{\s*id:\s*fadeoutTimer\s*\n\s*interval:\s*)\d+",
    re.DOTALL,
)


def apply_lock_tokens(
    *,
    name: str,
    vendor_path: Path,
    manifest: Manifest,
    patch: LockPatch,
) -> str:
    """Apply ``patch`` to a lock-screen QML file.

    Currently rewrites the ``fadeoutTimer`` interval (seconds → ms) for
    ``on_idle_dim_seconds``. If the file has no ``fadeoutTimer`` this is
    a no-op skip (the file may simply not declare it).

    A sentinel *comment* region records that the file is managed by
    usurface (for drift detection / restore). The interval edit itself
    is outside the sentinel region but is normalized in
    :func:`drift.strip_sentinels` so it doesn't register as drift.

    Returns a one-line description of the action taken.
    """
    if not vendor_path.exists():
        raise FileNotFoundError(vendor_path)

    pristine = extract.read_pristine(name)
    if pristine is None:
        raise RuntimeError(
            f"no pristine template stored for {name}; run 'usurface install' first"
        )

    text = vendor_path.read_text(encoding="utf-8", errors="replace")

    # Rewrite the fadeoutTimer interval: seconds → milliseconds.
    interval_ms = patch.on_idle_dim_seconds * 1000
    new_text, n = _FADEOUT_TIMER_INTERVAL_RE.subn(
        rf"\g<1>{interval_ms}", text, count=1
    )

    if n == 0 and not _file_with_sentinels(text):
        return f"{name}: no fadeoutTimer present; skipped"

    # Sentinel marker (comment-only, valid QML) for drift tracking.
    marker = (
        f"// on_idle_dim_seconds={patch.on_idle_dim_seconds} "
        f"suppress_wake_keypress={patch.suppress_wake_keypress}\n"
    )
    new_text = _ensure_sentinels(new_text, marker)

    if new_text == text:
        return f"{name}: no change"

    new_bytes = new_text.encode("utf-8")
    write_tracked(manifest, vendor_path, new_bytes, mode=0o644)
    return f"{name}: wrote {len(new_bytes)} bytes (sha {sha256_bytes(new_bytes)[:12]}…)"
