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
from pydantic import BaseModel, ConfigDict, Field

from trinity.providers import FetchedImage, ProviderError

_METADATA_URL = "https://www.bing.com/HPImageArchive.aspx"
# Maximum image download size. Bing POTD JPEGs are ~1-2 MiB; 50 MiB is a
# generous ceiling that still prevents an unbounded/malicious response
# from exhausting memory.
_MAX_IMAGE_BYTES = 50 * 1024 * 1024

# Defaults used when fetch() is called directly (not via the schema-validated
# pipeline). The schema in BingOptions is the source of truth for validation;
# these are just the no-args defaults so direct tests work.
_DEFAULT_OPTIONS: dict[str, Any] = {
    "mkt": "en-US",
    "resolution": "1920x1080",
    "index": 0,
    "timeout": 30.0,
}


class BingOptions(BaseModel):
    """Validated options for the Bing provider."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mkt: str = Field(
        default="en-US",
        description="Bing market code (e.g. en-US, en-GB, ja-JP).",
    )
    resolution: str = Field(
        default="1920x1080",
        description="Requested resolution (WxH).",
        pattern=r"^\d+x\d+$",
    )
    index: int = Field(
        default=0,
        description="Day offset (0 = today, 1 = yesterday, …).",
        ge=0,
    )
    timeout: float = Field(
        default=30.0,
        description="Per-request timeout in seconds.",
        gt=0,
        le=300,
    )


def fetch(options: dict[str, Any]) -> FetchedImage:
    """Fetch today's Bing Picture of the Day as JPEG bytes.

    Options are pre-validated by :class:`BingOptions` at config load
    time; this function receives the validated dict.  Raises
    :class:`ProviderError` for runtime failures: network errors,
    non-2xx responses, unexpected metadata shapes, and downloads
    exceeding the size cap.
    """
    opts = {**_DEFAULT_OPTIONS, **options}
    timeout = float(opts["timeout"])
    index = int(opts["index"])

    params = {
        "format": "js",
        "idx": str(index),
        "n": "1",
        "mkt": str(opts["mkt"]),
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }

    try:
        from trinity.providers.builtin import _http

        meta = _http.fetch_metadata_json(
            _METADATA_URL, params=params, headers=headers, timeout=timeout
        )
    except httpx.HTTPError as exc:
        # Covers timeouts, DNS/connection failures, and 4xx/5xx statuses.
        # Wrapped so the CLI reports a clean provider failure instead of
        # an "unexpected error" traceback for a transient network problem.
        raise ProviderError(f"bing provider: HTTP request failed: {exc}") from exc

    try:
        image_meta = meta["images"][0]
        rel_url = image_meta["url"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"unexpected Bing metadata shape: {meta!r}") from exc

    if not isinstance(rel_url, str) or not rel_url.startswith("/"):
        raise ProviderError(f"Bing returned non-relative image URL: {rel_url!r}")
    image_url = "https://www.bing.com" + rel_url

    # Force the requested resolution if the URL has the placeholder.
    if "{resolution}" in image_url:
        image_url = image_url.replace("{resolution}", str(opts["resolution"]))

    from trinity.providers.builtin import _http

    data, content_type = _http.download_image(
        image_url, headers=headers, timeout=timeout
    )
    return FetchedImage(
        data=data,
        content_type=content_type,
        suggested_extension=".jpg",
    )
