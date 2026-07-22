"""QML patching for login + lock screens.

Strategy: replace the *values* of existing ``readonly property string``
font/theme-token declarations inside the vendor QML files. Plasma's own
lockscreen and SDDM Breeze files already declare:

    readonly property string fontFamily: "DejaVu Sans"
    readonly property string fontWeight: "Normal"
    readonly property string passwordCharacter: "*"
    readonly property string clockFormat: "hh:mm"

trinity rewrites the string literals in place. This keeps the QML a
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

from trinity.manifest import Manifest, sha256_bytes, write_tracked
from trinity.theme import extract
from trinity.theme.descriptors import (
    detect_plasma_version,
)

# Sentinel markers; anything between them is replaced wholesale.
SENTINEL_START = "/* @trinity:start */"
SENTINEL_END = "/* @trinity:end */"
_HEADER = f"// managed by trinity — do not edit\n{SENTINEL_START}\n"
_FOOTER = f"\n{SENTINEL_END}\n"
# Exposed so the drift detector can strip the ``// managed by trinity``
# header that precedes the sentinel block. Without stripping it, the
# normalized on-disk file has an extra comment line that the stored
# pristine (which never has the header) does not, producing a
# false-positive drift after every apply.
HEADER_LINE = "// managed by trinity — do not edit"

# Marker comment that the wake-keypress guard writes into MainBlock.qml.
# Declared at module top so the fallback regex (which uses it via
# ``re.escape``) can be called at import time by the descriptor
# resolver, before the original definition site further down.
WAKE_GUARD_MARKER = "// @trinity:suppress_wake_keypress"


# --- Plasma-version-aware descriptor resolution -------------------------
#
# As of Phase 4 the QML anchor regexes below are *sourced* from the
# descriptor TOML files in :mod:`trinity.theme.descriptors`.  They
# remain re-exported under their original names so existing tests and
# callers keep working.  The resolution is:
#
# 1. On import, we attempt to compile each regex from the descriptor
#    that matches the *currently running* Plasma version (or, when
#    detection fails, from any descriptor with an empty ``plasma``
#    specifier — the wildcard fallback).
# 2. If no descriptor matches, the resolution falls through to the
#    hard-coded regexes below as a last-resort safety net.  This
#    keeps a developer machine without ``plasmashell`` on PATH (CI,
#    containers) functional.
# 3. ``_reset_descriptor_cache_for_tests`` (below) drops the cache so
#    tests that inject a fake descriptors directory see the change.


def _fadeout_fallback_pattern() -> re.Pattern[str]:
    return re.compile(
        r"(Timer\s*\{\s*id:\s*fadeoutTimer\b(?:[^{}]|\{[^{}]*\})*?interval:\s*)\d+",
        re.DOTALL,
    )


def _wake_anchor_fallback() -> re.Pattern[str]:
    return re.compile(
        r"(Keys\.onPressed:\s*event\s*=>\s*\{\n)"
        r"([ \t]*)(?=if \(event\.key === Qt\.Key_Left)"
    )


def _wake_guard_block_fallback() -> re.Pattern[str]:
    """Compiled regex for the wake-guard removal fallback.

    The wake-guard block is brace-balanced: an opening ``if (...) {``
    followed by zero or more inner statements (which themselves may
    contain braces, but in our case do not) and a closing ``}``. We
    match the whole block by anchoring on the marker comment, then
    walking forward through the text, counting ``{`` and ``}`` at
    column 0 of each line, until the depth returns to the opening
    level. This is robust to upstream reformatting that adds or
    removes blank lines or shuffles the inner statements.

    The returned ``re.Pattern`` matches a *prefix* of the block plus
    the marker comment; the caller must scan forward from the match
    end to consume the rest of the block. (Python ``re`` cannot match
    a balanced-brace expression directly.)
    """
    return re.compile(
        r"[ \t]*if \(!lockScreenRoot\.uiVisible[^\n]*"
        + re.escape(WAKE_GUARD_MARKER)
        + r"\n"
    )


def _balanced_block_end(text: str, start: int) -> int:
    """Return the index just past the closing ``}`` of a brace-balanced
    block starting at ``start``.

    ``start`` must point to the first character of the opening
    ``if (...) {`` line. The function walks forward line by line,
    tracking ``{``/``}`` depth (treating ``{`` and ``}`` in comments
    conservatively as code), until the depth returns to the level
    at ``start`` minus 1, then returns the position just past the
    matching ``}``.

    If no balanced close is found within ``text``, ``len(text)`` is
    returned (defensive: the caller treats the rest of the file as
    the block and a follow-up lint check rejects the result).
    """
    depth = 0
    i = start
    n = len(text)
    seen_open = False
    while i < n:
        ch = text[i]
        if ch == "{":
            depth += 1
            seen_open = True
        elif ch == "}":
            depth -= 1
            if seen_open and depth == 0:
                # Consume the rest of the line (newline if present).
                j = i + 1
                if j < n and text[j] == "\n":
                    j += 1
                return j
        i += 1
    return n


def _strip_wake_guard_block(text: str) -> str:
    """Remove a brace-balanced wake-guard block anchored on the marker.

    Used by :func:`_apply_wake_guard` when no descriptor matches the
    running Plasma version (CI, containerised runs) and the fallback
    regex has to do the work. Replaces the line-count-coupled
    `(?:[^\\n]*\\n){3}` pattern from earlier versions which broke
    when the upstream QML gained an extra blank line.
    """
    fallback = _wake_guard_block_fallback()
    m = fallback.search(text)
    if m is None:
        return text
    end = _balanced_block_end(text, m.start())
    return text[: m.start()] + text[end:]


# ---------------------------------------------------------------------------
# Backwards-compat alias. ``_WAKE_GUARD_BLOCK_RE`` is referenced by tests
# and by ``remove_sentinels``; it now returns the prefix-anchor pattern
# and the actual block-end trimming is done by ``_strip_wake_guard_block``.
# ---------------------------------------------------------------------------


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
    (seconds → milliseconds) in ``LockScreenUi.qml``.
    ``suppress_wake_keypress`` inserts a guard into the password box's
    ``Keys.onPressed`` handler in ``MainBlock.qml`` so the keypress that
    wakes the lock screen is consumed instead of being typed into the
    password field.
    """

    on_idle_dim_seconds: int
    suppress_wake_keypress: bool


# --- Module-level alias resolution ---------------------------------------
#
# These were the regex constants tests reference directly.  They are now
# resolved from descriptors on first access (cached per process) and
# re-exported under the same names for backward compatibility.  New
# code should call the descriptor functions directly (see below).
def _resolve_anchor_for(
    name: str, kind: str, *, field: str = "anchor"
) -> re.Pattern[str] | None:
    """Return the compiled regex for the given (name, kind) descriptor,
    or ``None`` if no descriptor matches the current Plasma version."""
    from trinity.theme.descriptors import _all as all_descriptors
    from trinity.theme.descriptors import _specifier_matches

    plasma = detect_plasma_version()
    version = plasma.version
    if version is None:
        return None
    for d in all_descriptors():
        if d.name != name:
            continue
        if not _specifier_matches(d, version):
            continue
        for p in d.patches:
            if p.kind != kind:
                continue
            anchor = getattr(p, field, None)
            if anchor is None:
                continue
            compiled: re.Pattern[str] = anchor.compile()
            return compiled
    return None


# --- Module-level lazy pattern resolution --------------------------------
#
# These were the regex constants tests and other modules reference
# directly.  They are now initialized with the hardcoded fallback
# patterns at import time (no ``detect_plasma_version()`` call), and
# upgraded to the descriptor-resolved patterns on first use via
# ``_get_pattern``.  This deferring ``detect_plasma_version()`` (which
# may shell out to ``plasmashell --version``) until the first actual
# patch operation.
#
# ``_reset_descriptor_cache_for_tests`` clears the cache so tests that
# inject a fake descriptors directory see the change on the next
# access.

_pattern_cache: dict[str, re.Pattern[str]] = {}


def _get_pattern(name: str, kind: str, *, field: str = "anchor") -> re.Pattern[str]:
    """Return the compiled regex for ``(name, kind, field)``.

    Resolved from the descriptor matching the running Plasma version
    on first access; cached for subsequent calls.  Falls back to the
    hardcoded regex if no descriptor matches.
    """
    cache_key = f"{name}:{kind}:{field}"
    if cache_key in _pattern_cache:
        return _pattern_cache[cache_key]
    pattern = _resolve_anchor_for(name, kind, field=field)
    if pattern is None:
        pattern = _fallback_for(name, kind, field)
    _pattern_cache[cache_key] = pattern
    return pattern


def _fallback_for(name: str, kind: str, field: str) -> re.Pattern[str]:
    """Return the hardcoded fallback pattern for ``(name, kind, field)``."""
    if name == "plasma_lockscreen_ui" and kind == "fadeout_timer":
        return _fadeout_fallback_pattern()
    if name == "plasma_lockscreen_mainblock" and kind == "wake_guard":
        if field == "remove_anchor":
            return _wake_guard_block_fallback()
        return _wake_anchor_fallback()
    # Should not happen — but return a never-matching pattern as a safety net.
    return re.compile(r"(?!)")


# Module-level constants: initialized with fallback patterns (no
# version detection at import time), upgraded lazily on first use.
# Other modules (e.g. ``drift.py``) import these directly.
_FADEOUT_TIMER_INTERVAL_RE: re.Pattern[str] = _fadeout_fallback_pattern()
_WAKE_HANDLER_ANCHOR_RE: re.Pattern[str] = _wake_anchor_fallback()
_WAKE_GUARD_BLOCK_RE: re.Pattern[str] = _wake_guard_block_fallback()


def _refresh_module_patterns() -> None:
    """Upgrade the module-level pattern constants from the fallback
    patterns to the descriptor-resolved patterns (if a matching
    descriptor exists for the running Plasma version).

    Called on first patch operation and by
    ``_reset_descriptor_cache_for_tests``.
    """
    global _FADEOUT_TIMER_INTERVAL_RE, _WAKE_HANDLER_ANCHOR_RE, _WAKE_GUARD_BLOCK_RE
    _FADEOUT_TIMER_INTERVAL_RE = _get_pattern("plasma_lockscreen_ui", "fadeout_timer")
    _WAKE_HANDLER_ANCHOR_RE = _get_pattern("plasma_lockscreen_mainblock", "wake_guard")
    _WAKE_GUARD_BLOCK_RE = _get_pattern(
        "plasma_lockscreen_mainblock", "wake_guard", field="remove_anchor"
    )


def _reset_descriptor_cache_for_tests() -> None:
    """Drop the descriptor cache so tests that injected a different
    descriptor set see the change on the next access.  Also clears the
    lazy pattern cache and re-resolves the module-level constants so
    the hardcoded-fallback vs. descriptor branch is observable.
    """
    from trinity.theme.descriptors import _reset_cache_for_tests

    _reset_cache_for_tests()
    _pattern_cache.clear()
    _refresh_module_patterns()


def _ensure_sentinels(text: str, block: str) -> str:
    """Ensure ``text`` contains the sentinel region with ``block`` inside.

    If the sentinel region already exists, replace its body with ``block``
    while preserving the ``// managed by trinity`` header comment that
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
        return pattern.sub(f"{SENTINEL_START}\n{block}\n{SENTINEL_END}", text)

    sep = "" if text.endswith("\n") else "\n"
    return f"{text}{sep}{_HEADER}{block}{_FOOTER}"


def _file_with_sentinels(text: str) -> bool:
    return SENTINEL_START in text and SENTINEL_END in text


_SENTINEL_BODY_RE = re.compile(
    re.escape(SENTINEL_START) + r"\n?(.*?)\n?" + re.escape(SENTINEL_END),
    re.DOTALL,
)


def _merged_marker_block(text: str, marker_line: str, key_prefix: str) -> str:
    """Merge ``marker_line`` into the existing sentinel body of ``text``.

    Both :func:`apply_font_tokens` and :func:`apply_lock_tokens` may
    manage the same file (the lock-screen UI), each recording one marker
    comment line. Replacing the whole sentinel body would clobber the
    other patcher's line, so instead the line starting with
    ``key_prefix`` is replaced in place (preserving line order so a
    re-patch is a true no-op) and other lines are kept verbatim. If no
    sentinel region exists yet, the block is just ``marker_line``.
    """
    marker = marker_line.rstrip("\n")
    existing: list[str] = []
    m = _SENTINEL_BODY_RE.search(text)
    if m:
        existing = [ln for ln in m.group(1).splitlines() if ln.strip()]

    out: list[str] = []
    replaced = False
    for ln in existing:
        if ln.startswith(key_prefix):
            if not replaced:
                out.append(marker)
                replaced = True
            # drop any duplicate lines with the same prefix
        else:
            out.append(ln)
    if not replaced:
        out.append(marker)
    return "\n".join(out) + "\n"


def apply_font_tokens(
    *,
    name: str,
    vendor_path: Path,
    manifest: Manifest,
    patch: FontPatch,
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
    property was changed, to record that the file is managed by trinity
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
            f"no pristine template stored for {name}; run 'trinity install' first"
        )

    text = vendor_path.read_text(encoding="utf-8", errors="replace")

    # Rewrite the value of each managed property declaration in place.
    new_text, replaced_count = _replace_property_values(text, patch)

    # If the file declares none of the managed properties, do nothing.
    # Appending a block to a QML file that doesn't expect it is a syntax
    # error (this was the root cause of the blue lock screen).
    if replaced_count == 0 and not _file_with_sentinels(text):
        return f"{name}: no managed properties present; skipped"

    marker = f"// fontFamily={patch.family} fontWeight={patch.weight}\n"
    block = _merged_marker_block(new_text, marker, "// fontFamily=")
    new_text = _ensure_sentinels(new_text, block)

    if new_text == text:
        return f"{name}: no change"

    new_bytes = new_text.encode("utf-8")
    write_tracked(manifest, vendor_path, new_bytes, mode=0o644)

    return f"{name}: wrote {len(new_bytes)} bytes (sha {sha256_bytes(new_bytes)[:12]}…)"


def _replace_property_values(text: str, patch: FontPatch) -> tuple[str, int]:
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
            r"((?:readonly\s+)?property\s+string\s+"
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


# --- lock-screen structural patching ------------------------------------

# The fadeoutTimer in LockScreenUi.qml controls how long the lock screen
# stays visible before dimming. Its interval is a literal in milliseconds.
# The regex tolerates other properties and a single ``onTriggered: { ... }``
# block between ``id: fadeoutTimer`` and ``interval:`` (the real vendor
# layout in Plasma 6). The previous ``[^}]*?`` body stopped at the first
# ``}`` — the opening brace of ``onTriggered: {`` — so the rewrite never
# actually fired and the timer interval was effectively never patched.
#
# (As of Phase 4 the actual compiled pattern is sourced from the
# descriptor TOML via ``_resolve_anchor_for`` above.  This comment
# documents the original semantics the descriptor captures.)

# suppress_wake_keypress: the password box in MainBlock.qml has
# ``focus: true``, so its ``Keys.onPressed`` attached handler (default
# priority BeforeItem) sees every keypress before the TextField inserts
# the character. The guard consumes a text-producing keypress that
# arrives while the lock-screen UI is hidden — waking the UI without
# typing a stray character into the password field. The insertion is
# anchored on the vendor handler's first statement (the Key_Left
# user-switch branch) so unrelated ``Keys.onPressed`` handlers are
# never touched. ``lockScreenRoot`` resolves via the QML context chain
# from the instantiating LockScreenUi.qml document.
# ``WAKE_GUARD_MARKER`` is declared at module top (above the
# descriptor-resolver block) so the fallback regex can reference it
# at import time.


def _wake_guard_block(indent: str) -> str:
    """Render the wake-keypress guard block at the given indentation.

    Sourced from the wake_guard descriptor's ``insert_block`` template
    when a matching descriptor is found; falls back to the original
    hard-coded block when no descriptor matches the current Plasma
    version (CI, containerised runs).
    """
    from trinity.theme.descriptors import _all as _all_descriptors
    from trinity.theme.descriptors import _specifier_matches

    plasma = detect_plasma_version()
    version = plasma.version
    if version is not None:
        for d in _all_descriptors():
            if d.name != "plasma_lockscreen_mainblock":
                continue
            if not _specifier_matches(d, version):
                continue
            for p in d.patches:
                if p.kind != "wake_guard":
                    continue
                return p.insert_block.replace("{indent}", indent)
    inner = indent + "    "
    return (
        f'{indent}if (!lockScreenRoot.uiVisible && event.text !== "") '
        f"{{ {WAKE_GUARD_MARKER}\n"
        f"{inner}lockScreenRoot.uiVisible = true;\n"
        f"{inner}event.accepted = true;\n"
        f"{inner}return;\n"
        f"{indent}}}\n"
    )


def _apply_wake_guard(text: str, *, enable: bool) -> tuple[str, bool]:
    """Insert or remove the wake-keypress guard.

    Returns ``(new_text, handler_present)`` where ``handler_present``
    is True when the file contains the password-box key handler (or an
    already-inserted guard) that this edit manages.
    """
    has_guard = WAKE_GUARD_MARKER in text
    if enable:
        if has_guard:
            return text, True
        m = _get_pattern("plasma_lockscreen_mainblock", "wake_guard").search(text)
        if m is None:
            return text, False
        indent = m.group(2)
        return (
            text[: m.end(1)] + _wake_guard_block(indent) + text[m.end(1) :],
            True,
        )
    if has_guard:
        # Always use the brace-balanced helper, not the descriptor's
        # ``remove_anchor`` regex. The descriptor's pattern is the
        # legacy line-count-coupled form (matches exactly 3 inner
        # lines), which is fragile against upstream reformatting. The
        # brace walker in :func:`_strip_wake_guard_block` is robust
        # to blank lines, reordered statements, and inner brace
        # blocks. Descriptor-driven ``remove_anchor`` is kept for
        # back-compat in the schema but ignored at runtime.
        return _strip_wake_guard_block(text), True
    return text, _get_pattern("plasma_lockscreen_mainblock", "wake_guard").search(
        text
    ) is not None


def apply_lock_tokens(
    *,
    name: str,
    vendor_path: Path,
    manifest: Manifest,
    patch: LockPatch,
) -> str:
    """Apply ``patch`` to a lock-screen QML file.

    Two structural edits, each a no-op if its anchor is absent from the
    file (the two live in different vendor files):

    - ``on_idle_dim_seconds`` rewrites the ``fadeoutTimer`` interval
      (seconds → ms) in ``LockScreenUi.qml``.
    - ``suppress_wake_keypress`` inserts (or, when false, removes) a
      guard in the password box's ``Keys.onPressed`` handler in
      ``MainBlock.qml`` that consumes the keypress waking a hidden UI.

    A sentinel *comment* region records that the file is managed by
    trinity (for drift detection / restore). The edits themselves are
    outside the sentinel region but are normalized in
    :func:`drift.strip_sentinels` so they don't register as drift.

    Returns a one-line description of the action taken.
    """
    if not vendor_path.exists():
        raise FileNotFoundError(vendor_path)

    pristine = extract.read_pristine(name)
    if pristine is None:
        raise RuntimeError(
            f"no pristine template stored for {name}; run 'trinity install' first"
        )

    text = vendor_path.read_text(encoding="utf-8", errors="replace")

    # Rewrite the fadeoutTimer interval: seconds → milliseconds.
    interval_ms = patch.on_idle_dim_seconds * 1000
    new_text, n_timer = _get_pattern("plasma_lockscreen_ui", "fadeout_timer").subn(
        rf"\g<1>{interval_ms}", text, count=1
    )

    # Insert/remove the wake-keypress guard in the password box handler.
    new_text, handler_present = _apply_wake_guard(
        new_text, enable=patch.suppress_wake_keypress
    )

    if n_timer == 0 and not handler_present and not _file_with_sentinels(text):
        return f"{name}: no managed lock-screen structures present; skipped"

    # Sentinel marker (comment-only, valid QML) for drift tracking.
    # Merged into the existing sentinel body so the font patcher's
    # marker line on the same file is preserved.
    marker = (
        f"// on_idle_dim_seconds={patch.on_idle_dim_seconds} "
        f"suppress_wake_keypress={str(patch.suppress_wake_keypress).lower()}\n"
    )
    block = _merged_marker_block(new_text, marker, "// on_idle_dim_seconds=")
    new_text = _ensure_sentinels(new_text, block)

    if new_text == text:
        return f"{name}: no change"

    new_bytes = new_text.encode("utf-8")
    write_tracked(manifest, vendor_path, new_bytes, mode=0o644)
    return f"{name}: wrote {len(new_bytes)} bytes (sha {sha256_bytes(new_bytes)[:12]}…)"


# --- Clock position patching (Feature 2) --------------------------------
#
# The clock position patcher repositions the clock item in SDDM Login.qml
# and the Plasma lock-screen QML based on user config.  It detects whether
# the clock is inside a layout (ColumnLayout, RowLayout, GridLayout, Flow)
# or an independent Item/Rectangle and generates the appropriate QML:
#
# - Layout-managed: Layout.alignment: Qt.Align<Direction>
# - Independent:    anchors.<side>: parent.<side>  (or anchors.centerIn: parent)
# - Coordinates:    x: N; y: N  (only for independent items)
#
# Existing dynamic bindings (visible, opacity tied to multiscreen pointer
# detection) are preserved — the patcher only adds lines, it never removes
# existing property assignments.

_LAYOUT_TYPES = ("ColumnLayout", "RowLayout", "GridLayout", "Flow")


def _alignment_to_qml_layout(alignment: str) -> str:
    """Map an alignment token to a QML Layout.alignment value."""
    mapping = {
        "top": "Qt.AlignTop",
        "bottom": "Qt.AlignBottom",
        "left": "Qt.AlignLeft",
        "right": "Qt.AlignRight",
        "center": "Qt.AlignHCenter | Qt.AlignVCenter",
        "top_left": "Qt.AlignTop | Qt.AlignLeft",
        "top_right": "Qt.AlignTop | Qt.AlignRight",
        "bottom_left": "Qt.AlignBottom | Qt.AlignLeft",
        "bottom_right": "Qt.AlignBottom | Qt.AlignRight",
    }
    return mapping.get(alignment, "Qt.AlignCenter")


def _alignment_to_qml_anchors(alignment: str) -> str:
    """Map an alignment token to QML anchors property assignments."""
    parts: list[str] = []
    if "top" in alignment and "bottom" not in alignment:
        parts.append("anchors.top: parent.top")
    if "bottom" in alignment:
        parts.append("anchors.bottom: parent.bottom")
    if "left" in alignment and "right" not in alignment and "center" not in alignment:
        parts.append("anchors.left: parent.left")
    if "right" in alignment:
        parts.append("anchors.right: parent.right")
    if alignment == "center":
        return "anchors.centerIn: parent"
    if not parts:
        return "anchors.centerIn: parent"
    return "\n        ".join(parts)


def _detect_clock_container(text: str, clock_id: str) -> str | None:
    """Detect if the clock item is inside a layout container.

    Returns the container type name (e.g. "ColumnLayout") or None if
    the clock is an independent Item/Rectangle.

    The heuristic examines the lines preceding the clock declaration
    for a layout container opening brace that has not been closed yet
    at the point where the clock item is declared.
    """
    lines = text.splitlines()
    clock_decl = f"id: {clock_id}"
    clock_line_idx = None
    for i, line in enumerate(lines):
        if clock_decl in line:
            clock_line_idx = i
            break

    if clock_line_idx is None:
        return None

    # Walk backward from the clock declaration, tracking brace depth.
    # We need to find the *enclosing block* of the block that contains
    # the clock.  When depth crosses -1 we found the clock's parent
    # block opener; when it crosses -2 we found the grandparent (the
    # layout container we're looking for).
    depth = 0
    for i in range(clock_line_idx - 1, -1, -1):
        line = lines[i]
        opens = line.count("{")
        closes = line.count("}")
        depth += closes
        depth -= opens

        # depth < 0 means we found an enclosing block opener.
        # The first one (depth=-1) is the clock's parent (e.g. Text {}).
        # The second one (depth=-2) is the grandparent (e.g. ColumnLayout {}).
        if depth < 0:
            for layout_type in _LAYOUT_TYPES:
                if layout_type in line:
                    return layout_type
            # If this is the first opener and it's not a layout,
            # keep going to check the next level up.
            if depth < -1:
                return None
    return None


def apply_clock_position_tokens(
    text: str,
    clock_id: str,
    position: ClockPosition,
) -> tuple[str, str]:
    """Apply clock position overrides to QML text.

    Returns ``(patched_text, message)``. When ``position.enabled`` is
    False, returns the text unchanged.

    The function detects whether the clock is inside a layout container
    (ColumnLayout, RowLayout, GridLayout, Flow) or an independent
    Item/Rectangle and generates appropriate QML:

    - Layout-managed clocks: ``Layout.alignment: Qt.Align<Direction>``
    - Independent clocks: ``anchors.<side>: parent.<side>`` or
      ``anchors.centerIn: parent``
    - Explicit coordinates (x, y): only applied to independent clocks
    """
    if not position.enabled:
        return text, "clock_position: disabled (no-op)"

    container = _detect_clock_container(text, clock_id)

    lines_to_inject: list[str] = []
    if container is not None:
        # Layout-managed: use Layout.alignment
        if position.alignment is not None:
            qml_align = _alignment_to_qml_layout(position.alignment)
            lines_to_inject.append(f"        Layout.alignment: {qml_align}")
    else:
        # Independent item: use anchors or coordinates
        if position.x is not None and position.y is not None:
            lines_to_inject.append(f"        x: {position.x}")
            lines_to_inject.append(f"        y: {position.y}")
        elif position.alignment is not None:
            anchor_lines = _alignment_to_qml_anchors(position.alignment)
            for line in anchor_lines.split("\n"):
                lines_to_inject.append(f"        {line}")

    if not lines_to_inject:
        return text, "clock_position: no alignment or coordinates set"

    # Find the clock item's opening line (the line with the clock's id)
    # and inject the alignment/anchor lines right after the id line.
    clock_decl = f"id: {clock_id}"
    lines = text.splitlines(keepends=True)
    injected = False
    new_lines: list[str] = []
    for line in lines:
        new_lines.append(line)
        if not injected and clock_decl in line:
            for inj_line in lines_to_inject:
                new_lines.append(inj_line + "\n")
            injected = True

    if not injected:
        return text, f"clock_position: clock id {clock_id!r} not found"

    result = "".join(new_lines)
    container_info = f"in {container}" if container else "as independent item"
    return result, f"clock_position: applied ({container_info})"


# Import here to avoid circular import at module level
from trinity.schema import ClockPosition  # noqa: E402
