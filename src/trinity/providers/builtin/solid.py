"""Solid-colour (or two-stop gradient) provider.

Options:
- ``color``: hex colour string (e.g. ``"#1d99f3"`` or ``"#abc"``).
- ``gradient_to``: optional second colour for a linear gradient.
- ``width``: pixels (default 1920).
- ``height``: pixels (default 1080).
- ``quality``: JPEG quality (default 85). Output is JPEG.
"""

from __future__ import annotations

import io
import re
from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator

from trinity.providers import FetchedImage, ProviderError

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Cap generated image dimensions to avoid unbounded memory allocation from
# a malicious or mistaken config. 8K (7680x4320) is the largest common
# desktop resolution and a generous ceiling.
_MAX_DIM = 7680

# Defaults used when fetch() is called directly (not via the schema-validated
# pipeline). The schema in SolidOptions is the source of truth for validation;
# these are just the no-args defaults so direct tests work.
_DEFAULT_OPTIONS: dict[str, Any] = {
    "color": "#1d99f3",
    "gradient_to": None,
    "width": 1920,
    "height": 1080,
    "quality": 85,
}


class SolidOptions(BaseModel):
    """Validated options for the solid-colour/gradient provider."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    color: str = Field(
        default="#1d99f3",
        description="Hex colour string (#RGB or #RRGGBB).",
    )
    gradient_to: str | None = Field(
        default=None,
        description="Optional second colour for a top→bottom gradient.",
    )
    width: int = Field(
        default=1920,
        description="Image width in pixels.",
        gt=0,
        le=_MAX_DIM,
    )
    height: int = Field(
        default=1080,
        description="Image height in pixels.",
        gt=0,
        le=_MAX_DIM,
    )
    quality: int = Field(
        default=85,
        description="JPEG quality (1-100).",
        ge=1,
        le=100,
    )

    @field_validator("color", "gradient_to")
    @classmethod
    def _check_hex(cls, value: str | None) -> str | None:
        if value is not None and not _HEX_RE.match(value):
            raise ValueError(f"invalid hex colour: {value!r}")
        return value


def _parse_color(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise ProviderError(f"color must be a string, got {type(value).__name__}")
    m = _HEX_RE.match(value)
    if not m:
        raise ProviderError(f"invalid color: {value!r} (want #RGB or #RRGGBB)")
    h = m.group(1)
    if len(h) == 3:
        r, g, b = (int(c * 2, 16) for c in h)
    else:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    return r, g, b


def _gradient_image(
    width: int,
    height: int,
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
) -> Image.Image:
    """Build a top→bottom linear-gradient RGB image.

    Uses Pillow's per-channel 8-bit gradient (``Image.linear_gradient``)
    and channel arithmetic to avoid the previous O(height) Python loop
    that drew one horizontal line per pixel row — that was unusably slow
    for 4K images. This approach is constant-time in Python and does the
    heavy lifting in C inside Pillow.
    """
    ramp = Image.linear_gradient("L").resize((1, height))
    # ``ramp`` is a (1 x height) column where row 0 = 0 (top) and the
    # last row = 255 (bottom). Use it as an alpha mask to blend two
    # solid colour planes.
    top_img = Image.new("RGB", (width, height), top)
    bottom_img = Image.new("RGB", (width, height), bottom)
    # Expand the 1px-wide ramp to full width so it can serve as a mask.
    mask = ramp.resize((width, height))
    return Image.composite(bottom_img, top_img, mask)


def fetch(options: dict[str, Any]) -> FetchedImage:
    """Generate a solid-colour or gradient JPEG.

    Options are pre-validated by :class:`SolidOptions` at config load
    time; this function receives the validated dict.  Raises
    :class:`ProviderError` only for runtime errors that schemas can't
    catch (e.g. Pillow internal failures).
    """
    opts = {**_DEFAULT_OPTIONS, **options}
    width = int(opts["width"])
    height = int(opts["height"])
    quality = int(opts["quality"])

    color = _parse_color(str(opts["color"]))

    gradient_to = opts.get("gradient_to")
    if gradient_to:
        end = _parse_color(str(gradient_to))
        img = _gradient_image(width, height, color, end)
    else:
        img = Image.new("RGB", (width, height), color)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return FetchedImage(
        data=buf.getvalue(),
        content_type="image/jpeg",
        suggested_extension=".jpg",
    )


def probe(options: dict[str, Any]) -> str:
    """Cheap change probe: a digest of the effective options.

    The generated image depends only on the options, so the token is
    stable until the config changes — ``apply --if-changed`` then skips
    regenerating and re-applying an identical image.
    """
    import hashlib
    import json

    opts = {**_DEFAULT_OPTIONS, **options}
    canonical = json.dumps(opts, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
