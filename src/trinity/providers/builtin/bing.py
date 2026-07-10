"""Bing Picture of the Day provider.

Fetches metadata from ``https://www.bing.com/HPImageArchive.aspx`` and
downloads the resulting JPEG. The metadata URL is the same one used by
many open-source Bing-POTD scripts and only requires a user-agent.

The provider expects these options:
- ``mkt``: market code (default ``"en-US"``).
- ``resolution``: requested resolution (default ``"1920x1080"``).
- ``index``: day offset (default ``0``).
- ``timeout``: per-request timeout in seconds (default 30).

Downloads are streamed and capped at ``_MAX_IMAGE_BYTES`` (50 MiB) to
prevent an unbounded download from filling memory/disk.
"""

from __future__ import annotations

from typing import Any

import httpx

from trinity.providers import FetchedImage, ProviderError

_METADATA_URL = "https://www.bing.com/HPImageArchive.aspx"
# Maximum image download size. Bing POTD JPEGs are ~1–2 MiB; 50 MiB is a
# generous ceiling that still prevents an unbounded/malicious response
# from exhausting memory.
_MAX_IMAGE_BYTES = 50 * 1024 * 1024
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

    with httpx.Client(
        timeout=timeout, headers=headers, follow_redirects=True
    ) as client:
        meta_resp = client.get(_METADATA_URL, params=params)
        meta_resp.raise_for_status()
        try:
            meta = meta_resp.json()
            image_meta = meta["images"][0]
            rel_url = image_meta["url"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected Bing metadata shape: {meta!r}") from exc

        if not isinstance(rel_url, str) or not rel_url.startswith("/"):
            raise ProviderError(f"Bing returned non-relative image URL: {rel_url!r}")
        image_url = "https://www.bing.com" + rel_url

        # Force the requested resolution if the URL has the placeholder.
        if "{resolution}" in image_url:
            image_url = image_url.replace("{resolution}", str(opts["resolution"]))

        # Stream the image in the same client so we reuse the connection
        # pool and cap the download size without loading it all at once.
        with client.stream("GET", image_url) as img_resp:
            img_resp.raise_for_status()
            # Check Content-Length up front when the server provides it.
            declared = img_resp.headers.get("content-length")
            if declared is not None:
                try:
                    if int(declared) > _MAX_IMAGE_BYTES:
                        raise ProviderError(
                            f"Bing image exceeds the {_MAX_IMAGE_BYTES}-byte "
                            f"download cap (Content-Length={declared})"
                        )
                except ValueError:
                    pass  # non-integer Content-Length; rely on byte count
            data = bytearray()
            for chunk in img_resp.iter_bytes():
                data.extend(chunk)
                if len(data) > _MAX_IMAGE_BYTES:
                    raise ProviderError(
                        f"Bing image exceeds the {_MAX_IMAGE_BYTES}-byte "
                        "download cap while streaming"
                    )
            content_type = img_resp.headers.get("content-type", "image/jpeg")

    return FetchedImage(
        data=bytes(data),
        content_type=content_type,
        suggested_extension=".jpg",
    )
