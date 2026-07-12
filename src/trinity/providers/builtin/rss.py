"""RSS/Atom image-feed provider.

Turns any RSS 2.0 or Atom feed that carries images into a wallpaper
source, so new sources (NASA APOD, Smithsonian, museum feeds, ...) are
a config recipe instead of a Python plugin.

The provider fetches the feed document, selects one item (``index``,
0 = first item in document order, which feeds publish newest-first),
resolves that item's image URL, and downloads it.

Image URL resolution, in precedence order:

1. RSS 2.0 ``<enclosure url=... type="image/*">``.
2. Media RSS ``<media:content url=...>`` with an image ``medium``/
   ``type`` (directly on the item or inside ``<media:group>``).
3. Media RSS ``<media:thumbnail url=...>`` (again, item or group).
4. Atom ``<link rel="enclosure" type="image/*" href=...>``.
5. The item ``<link>`` when its URL path ends in a known image
   extension (covers feeds that link straight at the image file).

Elements whose ``type``/``medium`` say "image" are always accepted; a
missing type is accepted only when the URL path has an image extension.
The orchestrator's ``verify_image`` step (Pillow decode + re-encode) is
the final gate on the actual bytes.

Security guardrails, inherited from :mod:`trinity.providers.builtin._http`
plus hardened XML parsing:

- HTTPS only for both the feed and the image URL.
- SSRF defense: private/loopback/link-local/reserved addresses are
  rejected on every request and every redirect hop.
- 5 MiB feed cap, 50 MiB image cap, redirect cap of 5.
- XML parsed with ``defusedxml``: entity expansion (billion laughs),
  external entities (XXE), and DTD retrieval are all rejected.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from defusedxml import ElementTree as DefusedET
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from trinity import __version__
from trinity.providers import FetchedImage, ProviderError
from trinity.providers.builtin import _http

_MEDIA_NS = "http://search.yahoo.com/mrss/"
_ATOM_NS = "http://www.w3.org/2005/Atom"
_NS = {"media": _MEDIA_NS, "atom": _ATOM_NS}

# Extensions accepted when an element carries no image type declaration.
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

# Descriptive User-Agent per RFC 9309 §2.2.1 etiquette: feed operators
# can identify (and rate-limit or contact) the client. Callers can
# override it via the ``headers`` option.
_USER_AGENT = (
    f"trinity-wallpaper/{__version__} (+https://github.com/MattCreigh/trinity)"
)


class RssOptions(BaseModel):
    """Validated options for the RSS/Atom feed provider."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: AnyHttpUrl = Field(
        description="HTTPS URL of the RSS 2.0 or Atom feed.",
    )
    index: int = Field(
        default=0,
        description="Item offset into the feed (0 = first/newest item).",
        ge=0,
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

    @field_validator("url")
    @classmethod
    def _https_only(cls, v: AnyHttpUrl) -> AnyHttpUrl:
        """Reject plain http:// — MITM tampering of the feed controls
        which image URL we then fetch."""
        if str(v).lower().startswith("http://"):
            raise ValueError("url must be https://, not http://")
        return v


def _parse_feed(content: bytes) -> ET.Element:
    """Parse feed bytes with defusedxml; wrap all failures as ProviderError.

    ``defusedxml`` raises ``DefusedXmlException`` subclasses (which
    extend ``ValueError``) for forbidden constructs — entity expansion,
    external entities, DTD retrieval — and ``ET.ParseError`` for
    malformed XML.
    """
    try:
        root = DefusedET.fromstring(content)
    except (ET.ParseError, ValueError) as exc:
        raise ProviderError(f"rss provider: feed is not safe/valid XML: {exc}") from exc
    if not isinstance(root, ET.Element):  # untyped defusedxml boundary
        raise ProviderError("rss provider: feed document is empty")
    return root


def _feed_items(root: ET.Element) -> list[ET.Element]:
    """Return the item/entry elements of an RSS 2.0 or Atom document."""
    if root.tag == "rss":
        channel = root.find("channel")
        if channel is None:
            raise ProviderError("rss provider: RSS feed has no <channel> element")
        return channel.findall("item")
    if root.tag == f"{{{_ATOM_NS}}}feed":
        return root.findall(f"{{{_ATOM_NS}}}entry")
    raise ProviderError(
        f"rss provider: unsupported feed root element {root.tag!r} "
        "(expected RSS 2.0 <rss> or Atom <feed>)"
    )


def _looks_like_image(type_attr: str | None, url: str) -> bool:
    """True if the type says image/*, or (no type) the URL path does."""
    if type_attr:
        return type_attr.strip().lower().startswith("image/")
    return urlparse(url).path.lower().endswith(_IMAGE_EXTENSIONS)


def _item_image_url(item: ET.Element) -> str | None:
    """Resolve one item's image URL using the documented precedence."""
    for enclosure in item.findall("enclosure"):
        url = enclosure.get("url")
        if url and _looks_like_image(enclosure.get("type"), url):
            return url
    for xpath in ("media:content", "media:group/media:content"):
        for content in item.findall(xpath, _NS):
            url = content.get("url")
            if not url:
                continue
            medium = (content.get("medium") or "").strip().lower()
            if medium == "image" or _looks_like_image(content.get("type"), url):
                return url
    for xpath in ("media:thumbnail", "media:group/media:thumbnail"):
        for thumbnail in item.findall(xpath, _NS):
            url = thumbnail.get("url")
            if url:
                return url
    for link in item.findall(f"{{{_ATOM_NS}}}link"):
        if link.get("rel") == "enclosure":
            href = link.get("href")
            if href and _looks_like_image(link.get("type"), href):
                return href
    link_el = item.find("link")
    if link_el is not None and link_el.text:
        candidate = link_el.text.strip()
        if _looks_like_image(None, candidate):
            return candidate
    return None


def _resolve_image_url(options: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Fetch + parse the feed and return the selected item's absolute
    image URL along with the validated options.

    Shared by :func:`fetch` and :func:`probe` so both apply identical
    validation and selection rules.
    """
    opts = RssOptions.model_validate(options).model_dump()
    feed_url = str(opts["url"])
    headers = {"User-Agent": _USER_AGENT, **opts["headers"]}

    try:
        content = _http.fetch_metadata_bytes(
            feed_url, headers=headers, timeout=float(opts["timeout"])
        )
    except httpx.HTTPError as exc:
        # Covers timeouts, DNS/connection failures; wrapped so the CLI
        # reports a clean provider failure for a transient network problem.
        raise ProviderError(f"rss provider: HTTP request failed: {exc}") from exc

    items = _feed_items(_parse_feed(content))
    if not items:
        raise ProviderError(f"rss provider: feed {feed_url} contains no items")
    index = int(opts["index"])
    if index >= len(items):
        raise ProviderError(
            f"rss provider: feed has {len(items)} item(s); "
            f"index {index} is out of range"
        )
    raw_url = _item_image_url(items[index])
    if raw_url is None:
        raise ProviderError(
            f"rss provider: feed item {index} declares no image "
            "(no enclosure / media:content / media:thumbnail / image link)"
        )
    # Resolve relative URLs against the feed URL.
    return urljoin(feed_url, raw_url), opts


def fetch(options: dict[str, Any]) -> FetchedImage:
    """Fetch the selected feed item's image."""
    image_url, opts = _resolve_image_url(options)
    headers = {"User-Agent": _USER_AGENT, **opts["headers"]}
    try:
        data, content_type = _http.download_image(
            image_url, headers=headers, timeout=float(opts["timeout"])
        )
    except httpx.HTTPError as exc:
        raise ProviderError(f"rss provider: HTTP request failed: {exc}") from exc
    return FetchedImage(
        data=data,
        content_type=content_type,
        suggested_extension=_http.extension_for_content_type(content_type),
    )


def probe(options: dict[str, Any]) -> str:
    """Cheap change probe: fetch only the feed (a few KiB, no image).

    The change token is the resolved absolute image URL — feeds publish
    a new URL per image, so a stable token means an unchanged wallpaper.
    """
    image_url, _opts = _resolve_image_url(options)
    return image_url
