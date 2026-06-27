"""QML patching for login + lock screens.

Hybrid strategy (see PLAN §10.2):

* The first patch produces a unified diff against the pristine
  template and applies it, then writes sentinel markers into the
  on-disk file.
* Subsequent patches only substitute the region between the sentinels.
* ``usurface restore`` removes the sentinels and rewrites the file
  back to the pristine template content.

For v1 we expose two operations:

* :func:`apply_font_tokens` — replaces font, weight, password
  character, and clock format within the sentinel region.
* :func:`remove_sentinels` — strips the sentinel region and rewrites
  the file from the stored pristine template.
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

    def render_block(self) -> str:
        return (
            f'pragma Singleton\nimport QtQuick\n\n'
            f'QtObject {{\n'
            f'    readonly property string fontFamily: "{self.family}"\n'
            f'    readonly property string fontWeight: "{self.weight}"\n'
            f'    readonly property string passwordCharacter: "{self.password_character}"\n'
            f'    readonly property string clockFormat: "{self.clock_format}"\n'
            f'}}\n'
        )


def _ensure_sentinels(text: str, block: str) -> str:
    """Ensure ``text`` contains the sentinel region with ``block`` inside.

    If the sentinel region already exists, replace its body with ``block``.
    Otherwise append the region as a new comment block.
    """
    if SENTINEL_START in text and SENTINEL_END in text:
        pattern = re.compile(
            re.escape(SENTINEL_START) + r".*?" + re.escape(SENTINEL_END),
            re.DOTALL,
        )
        return pattern.sub(f"{SENTINEL_START}\n{block}{SENTINEL_END}", text)

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
    """Write ``patch`` into the sentinel region of ``vendor_path``.

    If the file has no sentinels yet and ``require_sentinels`` is False,
    the sentinel region is appended.

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
    has_sentinels = _file_with_sentinels(text)
    if not has_sentinels:
        if require_sentinels:
            raise RuntimeError(
                f"{vendor_path} does not contain sentinels and require_sentinels=True"
            )
        text = _ensure_sentinels(text, "")  # empty block; we'll fill it next

    new_text = _ensure_sentinels(text, patch.render_block())
    if new_text == text:
        return f"{name}: no change"

    new_bytes = new_text.encode("utf-8")
    write_tracked(manifest, vendor_path, new_bytes, mode=0o644)

    return f"{name}: wrote {len(new_bytes)} bytes (sha {sha256_bytes(new_bytes)[:12]}…)"


def remove_sentinels(*, name: str, vendor_path: Path, manifest: Manifest) -> str:
    """Strip the sentinel region and restore the file to pristine content."""
    pristine = extract.read_pristine(name)
    if pristine is None:
        raise RuntimeError(f"no pristine template stored for {name}")
    if not vendor_path.exists():
        raise FileNotFoundError(vendor_path)

    write_tracked(manifest, vendor_path, pristine, mode=0o644)
    return f"{name}: restored to pristine"
