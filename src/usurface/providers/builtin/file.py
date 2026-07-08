"""Local-file provider.

Expects the option ``path``. Returns the file's bytes verbatim.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from usurface.providers import FetchedImage, ProviderError

# Refuse to read a local image larger than this into memory before
# decoding/re-encoding. The orchestrator's verify_image step decodes the
# full image with Pillow, so an enormous local file (e.g. a 2 GB TIFF)
# could exhaust memory on a low-RAM laptop. 100 MiB is generous for any
# real wallpaper and still bounds the worst case.
_MAX_LOCAL_BYTES = 100 * 1024 * 1024

_DEFAULT_OPTIONS: dict[str, Any] = {}


def fetch(options: dict[str, Any]) -> FetchedImage:
    opts = {**_DEFAULT_OPTIONS, **options}
    raw = opts.get("path")
    if not isinstance(raw, str) or not raw:
        raise ProviderError("file provider requires a 'path' option")

    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_file():
        raise ProviderError(f"file provider: file not found: {path}")

    size = path.stat().st_size
    if size > _MAX_LOCAL_BYTES:
        raise ProviderError(
            f"file provider: {path} is {size} bytes which exceeds the "
            f"{_MAX_LOCAL_BYTES}-byte local-file cap"
        )

    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        content_type = "image/jpeg"
        ext = ".jpg"
    elif suffix == ".png":
        content_type = "image/png"
        ext = ".png"
    elif suffix == ".webp":
        content_type = "image/webp"
        ext = ".webp"
    else:
        content_type = "application/octet-stream"
        ext = suffix or ".bin"
    return FetchedImage(data=data, content_type=content_type, suggested_extension=ext)
