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

from PIL import Image, ImageDraw

from usurface.providers import FetchedImage, ProviderError

_DEFAULT_OPTIONS: dict[str, Any] = {
    "color": "#1d99f3",
    "gradient_to": None,
    "width": 1920,
    "height": 1080,
    "quality": 85,
}

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


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


def fetch(options: dict[str, Any]) -> FetchedImage:
    opts = {**_DEFAULT_OPTIONS, **options}
    width = int(opts["width"])
    height = int(opts["height"])
    if width <= 0 or height <= 0:
        raise ProviderError(f"invalid dimensions: {width}x{height}")

    color = _parse_color(str(opts["color"]))
    img = Image.new("RGB", (width, height), color)

    gradient_to = opts.get("gradient_to")
    if gradient_to:
        end = _parse_color(str(gradient_to))
        # Linear gradient top -> bottom.
        draw = ImageDraw.Draw(img)
        for y in range(height):
            ratio = y / max(1, height - 1)
            r = int(color[0] + (end[0] - color[0]) * ratio)
            g = int(color[1] + (end[1] - color[1]) * ratio)
            b = int(color[2] + (end[2] - color[2]) * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

    quality = max(1, min(100, int(opts["quality"])))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return FetchedImage(
        data=buf.getvalue(),
        content_type="image/jpeg",
        suggested_extension=".jpg",
    )
