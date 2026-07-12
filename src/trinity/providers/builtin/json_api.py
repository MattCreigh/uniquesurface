"""Generic JSON-API wallpaper provider.

Most POTD APIs follow the same pattern: GET a metadata JSON document,
extract an image URL via a JSON Pointer, then download the image. This
provider turns that pattern into a config recipe rather than requiring
a Python plugin for every API.

Recipe fields (all required, validated at config-load time):

- ``metadata_url``: HTTPS URL of the JSON metadata document.
- ``image_url_pointer``: RFC 6901 JSON Pointer into the metadata; must
  resolve to a string (absolute or relative URL).
- ``params``: optional query string to add to the metadata request.
- ``headers``: optional HTTP headers (e.g. ``User-Agent``).
- ``timeout``: per-request timeout in seconds (default 30, max 300).

Security guardrails are inherited from :mod:`trinity.providers.builtin._http`:

- HTTPS only for both metadata and image URLs.
- SSRF defense: a pre-flight DNS check rejects private/loopback/
  link-local/reserved addresses on every request (and on every
  redirect hop).
- Redirect cap of 5.
- 5 MiB metadata cap, 50 MiB image cap.
- Header and param count/length caps; no header echo of secrets.

Recipes (verified against the providers' public APIs at the time of
writing; if any API moves, just update the config):

- NASA APOD: needs an API key (DEMO_KEY works for limited use).
- Wikimedia POTD: no key required.
- Bing: use the built-in ``bing`` provider (it has a fixed URL shape).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from trinity.providers import FetchedImage, ProviderError
from trinity.providers.builtin import _http

_DEFAULT_OPTIONS: dict[str, Any] = {}


class JsonApiOptions(BaseModel):
    """Validated options for the JSON-API provider.

    The URL fields use pydantic's ``AnyHttpUrl`` to validate scheme +
    structure at config load time.  HTTPS-only enforcement and SSRF
    checks happen at request time (in :mod:`_http`).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    metadata_url: AnyHttpUrl = Field(
        description="HTTPS URL of the JSON metadata document.",
    )
    image_url_pointer: str = Field(
        description=(
            "RFC 6901 JSON Pointer into the metadata; must resolve to a "
            "string URL (absolute or relative). Example: '/image/url'."
        ),
        min_length=1,
    )
    params: dict[str, str] = Field(
        default_factory=dict,
        description="Optional query string for the metadata request.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Optional HTTP headers for both requests.",
    )
    timeout: float = Field(
        default=30.0,
        description="Per-request timeout in seconds.",
        gt=0,
        le=300,
    )

    @field_validator("metadata_url")
    @classmethod
    def _https_only(cls, v: AnyHttpUrl) -> AnyHttpUrl:
        """Reject plain http:// — MITM tampering is a real threat for the
        metadata response (it controls the image URL we then fetch)."""
        if str(v).lower().startswith("http://"):
            raise ValueError("metadata_url must be https://, not http://")
        return v


def _resolve_image_url(options: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Fetch the metadata document and return the absolute image URL.

    Shared by :func:`fetch` and :func:`probe` so both apply identical
    validation and pointer-resolution rules.  Validates once at the
    entry point so config errors surface here (not deep in the
    SSRF/HTTP layer).
    """
    opts = JsonApiOptions.model_validate(options).model_dump()

    meta = _http.fetch_metadata_json(
        str(opts["metadata_url"]),
        params=opts.get("params", {}),
        headers=opts.get("headers", {}),
        timeout=float(opts["timeout"]),
    )
    raw_url = _http.resolve_pointer(meta, str(opts["image_url_pointer"]))
    if not isinstance(raw_url, str):
        raise ProviderError(
            f"JSON pointer {opts['image_url_pointer']!r} resolved to "
            f"non-string ({type(raw_url).__name__})"
        )

    # Resolve relative URLs against the metadata URL.
    return urljoin(str(opts["metadata_url"]), raw_url), opts


def fetch(options: dict[str, Any]) -> FetchedImage:
    """Fetch a wallpaper via the generic JSON metadata → image URL recipe."""
    image_url, opts = _resolve_image_url(options)

    data, content_type = _http.download_image(
        image_url,
        headers=opts.get("headers", {}),
        timeout=float(opts["timeout"]),
    )
    return FetchedImage(
        data=data,
        content_type=content_type,
        suggested_extension=_http.extension_for_content_type(content_type),
    )


def probe(options: dict[str, Any]) -> str:
    """Cheap change probe: fetch only the metadata document.

    The token is the resolved absolute image URL — POTD APIs publish a
    new URL per image, so a stable token means an unchanged wallpaper.
    """
    image_url, _opts = _resolve_image_url(options)
    return image_url
