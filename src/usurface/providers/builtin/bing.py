"""Bing Picture of the Day provider.

Fetches metadata from ``https://www.bing.com/HPImageArchive.aspx`` and
downloads the resulting JPEG. The metadata URL is the same one used by
many open-source Bing-POTD scripts and only requires a user-agent.

The provider expects these options:
- ``mkt``: market code (default ``"en-US"``).
- ``resolution``: requested resolution (default ``"1920x1080"``).
- ``index``: day offset (default ``0``).
- ``timeout``: per-request timeout in seconds (default 30).
"""

from __future__ import annotations

from typing import Any

import httpx

from usurface.providers import FetchedImage, ProviderError

_METADATA_URL = "https://www.bing.com/HPImageArchive.aspx"
_DEFAULT_OPTIONS: dict[str, Any] = {
    "mkt": "en-US",
    "resolution": "1920x1080",
    "index": 0,
    "timeout": 30.0,
}


def fetch(options: dict[str, Any]) -> FetchedImage:
    opts = {**_DEFAULT_OPTIONS, **options}
    timeout = float(opts["timeout"])

    params = {
        "format": "js",
        "idx": str(int(opts["index"])),
        "n": "1",
        "mkt": str(opts["mkt"]),
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }

    with httpx.Client(timeout=timeout, headers=headers) as client:
        meta_resp = client.get(_METADATA_URL, params=params)
        meta_resp.raise_for_status()
        meta = meta_resp.json()

    try:
        image_meta = meta["images"][0]
        rel_url = image_meta["url"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"unexpected Bing metadata shape: {meta!r}") from exc

    image_url = "https://www.bing.com" + rel_url

    # Force the requested resolution if the URL has the {resolution} placeholder.
    if "{resolution}" in image_url:
        image_url = image_url.replace("{resolution}", str(opts["resolution"]))

    with httpx.Client(timeout=timeout, headers=headers) as client:
        img_resp = client.get(image_url)
        img_resp.raise_for_status()
        data = img_resp.content

    return FetchedImage(
        data=data,
        content_type=img_resp.headers.get("content-type", "image/jpeg"),
        suggested_extension=".jpg",
    )
