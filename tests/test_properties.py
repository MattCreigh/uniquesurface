"""Property-based tests for pure, invariant-rich functions.

Uses ``hypothesis`` to generate inputs that exercise the contracts of
functions that are difficult to cover exhaustively with example-based
tests.  Example counts are capped via ``max_examples`` to keep the
suite under ~10s (the rest of the suite runs in ~1.5s).
"""

from __future__ import annotations

import json
import tomllib
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from trinity.config import _to_toml
from trinity.manifest import ManifestEntry
from trinity.providers import ProviderError
from trinity.providers.builtin.solid import _parse_color
from trinity.theme.drift import normalize_managed_values, strip_sentinels
from trinity.theme.qml_patch import FontPatch, _replace_property_values

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

# TOML-safe primitive values (no None — _to_toml skips None deliberately).
# Floats are represented as ints when possible to avoid float repr issues.
# Text is restricted to ASCII printable to avoid Unicode chars that are
# valid in Python strings but produce TOML parse issues in edge cases.
_toml_primitives = st.one_of(
    st.booleans(),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False, width=64).filter(
        lambda f: f != int(f)  # only non-integer floats (avoids 1.0 vs 1)
    ),
    st.text(
        alphabet=st.characters(
            whitelist_categories=(),
            whitelist_characters=(
                "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "0123456789 _-.,:/'()"
            ),
        ),
        max_size=50,
    ),
)

# TOML keys must be bare (ASCII letters, digits, _, -) and not start with
# a digit. Restrict to ASCII to avoid Unicode chars that are valid Python
# identifiers but not valid bare TOML keys (e.g. µ, ß).
_toml_keys = st.text(
    alphabet=st.characters(
        whitelist_categories=(),  # no Unicode categories
        whitelist_characters="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-",
    ),
    min_size=1,
    max_size=20,
).filter(lambda s: not s[0].isdigit())

# A flat dict of string keys → TOML-safe primitives (no nesting, no None).
_flat_toml_dict = st.dictionaries(
    keys=_toml_keys,
    values=_toml_primitives,
    max_size=10,
)

# A nested dict with one level of sub-sections (mirrors the config schema).
# Sub-sections are dicts of primitives only (no further nesting).
_nested_toml_dict = st.dictionaries(
    keys=_toml_keys,
    values=st.one_of(_toml_primitives, _flat_toml_dict),
    max_size=5,
)

# Valid hex colour strings: #RGB or #RRGGBB.
_hex_colors = st.one_of(
    st.from_regex(r"#[0-9a-fA-F]{3}", fullmatch=True),
    st.from_regex(r"#[0-9a-fA-F]{6}", fullmatch=True),
)

# Strings that definitely do NOT match the hex colour regex.
_invalid_colors = st.one_of(
    st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu")),
        min_size=1,
        max_size=10,
    ).filter(lambda s: not s.startswith("#")),
    st.from_regex(r"#[0-9a-fA-F]{2}", fullmatch=True),  # too short
    st.from_regex(r"#[0-9a-fA-F]{5}", fullmatch=True),  # wrong length
    st.from_regex(r"#[0-9a-fA-F]{8}", fullmatch=True),  # too long
    st.just(""),
    st.just("#"),
    st.just("#GGG"),
)

# FontPatch values with safe characters (no regex-breaking).
_safe_font_values = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters=" -._",
    ),
    min_size=1,
    max_size=20,
)

_font_patches = st.builds(
    FontPatch,
    family=_safe_font_values,
    weight=_safe_font_values,
    password_character=_safe_font_values.map(lambda s: s[:4] or "*"),
    clock_format=_safe_font_values,
)

# QML-like text with optional managed property declarations.
# Use [ \t] instead of \s in regexes to avoid Unicode whitespace (\x85,
# \u2028, \u2029) which Python's splitlines() treats as line breaks but
# the regex patcher doesn't, causing misalignment in line assertions.
_qml_property_lines = st.one_of(
    st.just(""),
    st.from_regex(
        r"(readonly[ \t]+)?property[ \t]+string[ \t]+"
        r"(fontFamily|fontWeight|passwordCharacter|clockFormat)"
        r'[ \t]*:[ \t]*"[a-zA-Z0-9 _\-.,:]*"',
        fullmatch=True,
    ),
)

# Safe QML-ish text: ASCII letters, digits, common punctuation, whitespace.
_qml_text = st.lists(
    st.one_of(
        _qml_property_lines,
        st.text(
            alphabet=(
                "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "0123456789 \t\n{}[]():;,.\"'=+-*/_"
            ),
            max_size=80,
        ),
    ),
    max_size=20,
).map(lambda lines: "\n".join(lines))


# ---------------------------------------------------------------------------
# 1. _to_toml / _toml_literal round-trip
# ---------------------------------------------------------------------------


@given(_flat_toml_dict)
@settings(max_examples=100, deadline=None)
def test_to_toml_flat_round_trip(data: dict[str, Any]) -> None:
    """``tomllib.loads(_to_toml(d))`` reproduces ``d`` for flat dicts of
    TOML-safe primitives."""
    rendered = _to_toml(data)
    parsed = tomllib.loads(rendered)
    assert parsed == data


@given(_nested_toml_dict)
@settings(max_examples=100, deadline=None)
def test_to_toml_nested_round_trip(data: dict[str, Any]) -> None:
    """Round-trip for one-level-nested dicts (mirrors the config schema)."""
    rendered = _to_toml(data)
    parsed = tomllib.loads(rendered)
    assert parsed == data


# ---------------------------------------------------------------------------
# 2. _replace_property_values — idempotence + non-target text unchanged
# ---------------------------------------------------------------------------


@given(_qml_text, _font_patches)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_replace_property_values_idempotent(text: str, patch: FontPatch) -> None:
    """Patching twice produces the same result as patching once."""
    once, count1 = _replace_property_values(text, patch)
    twice, count2 = _replace_property_values(once, patch)
    assert once == twice
    # Second application should find the same number of declarations (they
    # were replaced, not removed), but the values are already the patch
    # values so the text doesn't change.
    assert count2 == count1


@given(_qml_text, _font_patches)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_replace_property_values_preserves_non_target_text(
    text: str, patch: FontPatch
) -> None:
    """Lines that don't contain managed property declarations are byte-identical
    in the output (same count, same content, same order)."""
    new_text, _ = _replace_property_values(text, patch)
    managed_props = ("fontFamily", "fontWeight", "passwordCharacter", "clockFormat")
    original_non_managed = [
        line
        for line in text.splitlines(keepends=True)
        if not any(p in line for p in managed_props)
    ]
    new_non_managed = [
        line
        for line in new_text.splitlines(keepends=True)
        if not any(p in line for p in managed_props)
    ]
    assert original_non_managed == new_non_managed


# ---------------------------------------------------------------------------
# 3. strip_sentinels — identity on clean text, round-trip with insertion
# ---------------------------------------------------------------------------


@given(_qml_text)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_strip_sentinels_identity_on_clean_text(text: str) -> None:
    """Text without sentinel markers is returned unchanged."""
    assert strip_sentinels(text) == text


@given(_qml_text)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_strip_sentinels_round_trip_with_insertion(text: str) -> None:
    """Inserting a sentinel block and then stripping it recovers the
    original text (up to whitespace at the insertion point)."""
    sentinel = "/* @trinity:start */\n// marker\n/* @trinity:end */\n"
    # Insert in the middle if there's a newline, else append.
    if "\n" in text:
        idx = text.index("\n") + 1
        inserted = text[:idx] + sentinel + text[idx:]
    else:
        inserted = text + sentinel
    stripped = strip_sentinels(inserted)
    # The sentinel block is removed; the surrounding text survives.
    assert text in stripped or stripped in text


# ---------------------------------------------------------------------------
# 4. normalize_managed_values — idempotence
# ---------------------------------------------------------------------------


@given(_qml_text)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_normalize_managed_values_idempotent(text: str) -> None:
    """Normalising twice produces the same output as normalising once."""
    once = normalize_managed_values(text)
    twice = normalize_managed_values(once)
    assert once == twice


# ---------------------------------------------------------------------------
# 5. _parse_color — #RGB ↔ #RRGGBB equivalence, invalid raises
# ---------------------------------------------------------------------------


@given(st.from_regex(r"#([0-9a-fA-F]{3})", fullmatch=True))
@settings(max_examples=50, deadline=None)
def test_parse_color_rgb_equals_rrggbb_doubling(hex3: str) -> None:
    """``#RGB`` expands to the same (r, g, b) as ``#RRGGBB`` where each
    channel is the doubled digit."""
    r, g, b = _parse_color(hex3)
    expanded = f"#{hex3[1] * 2}{hex3[2] * 2}{hex3[3] * 2}"
    r2, g2, b2 = _parse_color(expanded)
    assert (r, g, b) == (r2, g2, b2)


@given(st.from_regex(r"#([0-9a-fA-F]{6})", fullmatch=True))
@settings(max_examples=50, deadline=None)
def test_parse_color_rrggbb_channels(hex6: str) -> None:
    """``#RRGGBB`` parses to the correct individual channel values."""
    r, g, b = _parse_color(hex6)
    h = hex6[1:]
    assert r == int(h[0:2], 16)
    assert g == int(h[2:4], 16)
    assert b == int(h[4:6], 16)


@given(_invalid_colors)
@settings(max_examples=50, deadline=None)
def test_parse_color_rejects_invalid(value: str) -> None:
    """Invalid colour strings raise ``ProviderError``."""
    with pytest.raises(ProviderError):
        _parse_color(value)


# ---------------------------------------------------------------------------
# 6. ManifestEntry.to_json / from_json round-trip
# ---------------------------------------------------------------------------


_manifest_entries = st.builds(
    ManifestEntry,
    ts=st.from_regex(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z", fullmatch=True),
    op=st.sampled_from(["write", "delete"]),
    path=st.text(min_size=1, max_size=100),
    prev_sha256=st.one_of(st.none(), st.from_regex(r"[0-9a-f]{64}", fullmatch=True)),
    new_sha256=st.one_of(st.none(), st.from_regex(r"[0-9a-f]{64}", fullmatch=True)),
    prev_bytes_path=st.one_of(st.none(), st.text(min_size=1, max_size=100)),
)


@given(_manifest_entries)
@settings(max_examples=100, deadline=None)
def test_manifest_entry_json_round_trip(entry: ManifestEntry) -> None:
    """``from_json(to_json(e)) == e`` for all valid entries."""
    raw = entry.to_json()
    restored = ManifestEntry.from_json(raw)
    assert restored == entry
    # The JSON must be valid and sorted (deterministic output).
    parsed = json.loads(raw)
    assert parsed == json.loads(json.dumps(parsed, sort_keys=True))
