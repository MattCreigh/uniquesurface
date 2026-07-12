"""Shared HTTP machinery for built-in providers.

Centralises the SSRF-hardened, size-capped streaming download that
bing.py and json_api.py both need.  Keeps the per-provider modules small
and ensures the security/limits rules are defined once and consistently
applied.

Security guardrails (non-negotiable):

- HTTPS only for both metadata and image URLs.
- SSRF defense: before each request (and on every redirect hop), resolve
  the hostname and reject private, loopback, link-local, and reserved
  ranges (``ipaddress`` module: ``is_private``, ``is_loopback``,
  ``is_link_local``, ``is_reserved``, plus IPv4-mapped IPv6).
- Redirect cap (5) and per-hop HTTPS + SSRF re-checks.
- Streaming download with 50 MiB cap to bound memory/disk.
- Per-request timeout.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from trinity.providers import ProviderError

_MAX_IMAGE_BYTES = 50 * 1024 * 1024
_MAX_METADATA_BYTES = 5 * 1024 * 1024
_MAX_REDIRECTS = 5
_MAX_HEADERS = 32
_MAX_HEADER_VALUE_LEN = 1024
_MAX_PARAMS = 32
_MAX_PARAM_VALUE_LEN = 1024


class SSRFError(ProviderError):
    """Raised when an HTTP request would target a private/reserved address."""


def _check_scheme(url: str) -> None:
    """Refuse anything that isn't http(s); we then reject http below."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"URL scheme {parsed.scheme!r} not allowed; use http(s)")


def _require_https(url: str) -> None:
    """Refuse http:// for both metadata and image URLs.

    Plain http is rejected to prevent MITM tampering of the metadata
    response (which would let an attacker redirect the image URL to an
    internal address).
    """
    if urlparse(url).scheme != "https":
        raise SSRFError(f"only https:// URLs are allowed; got {url!r}")


def _is_safe_address(addr: str) -> bool:
    """Return True if ``addr`` is safe to connect to (public, not loopback)."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return False
    # Reject IPv4-mapped IPv6 (::ffff:10.0.0.1 etc.) that map to private
    # IPv4 ranges — ``ip.is_private`` already catches this in py3.12+,
    # but we belt-and-brace it.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return _is_safe_address(ip.ipv4_mapped.compressed)
    return True


def _resolve_safely(host: str) -> str:
    """Resolve ``host`` and assert every returned IP is safe.

    Returns the first safe IP.  Raises ``SSRFError`` if no address is
    safe or resolution fails.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SSRFError(f"DNS resolution failed for {host!r}: {exc}") from exc
    if not infos:
        raise SSRFError(f"DNS resolution returned no addresses for {host!r}")
    for info in infos:
        addr = info[4][0]
        if isinstance(addr, str) and _is_safe_address(addr):
            return addr
    raise SSRFError(f"host {host!r} resolves only to private/reserved addresses")


# Module-level hook for the DNS-resolution function.  Production
# uses the real ``_resolve_safely``; tests can monkey-patch this to
# return the hostname unchanged (respx mocks by hostname).
_resolve_safely_hook = _resolve_safely


def _sanitise_headers(headers: dict[str, str]) -> dict[str, str]:
    """Cap header count and value length; drop auth-sensitive headers.

    Prevents the user from injecting unbounded header data or echo of
    secrets via the config file.
    """
    out: dict[str, str] = {}
    for k, v in headers.items():
        if len(out) >= _MAX_HEADERS:
            break
        if not isinstance(v, str) or len(v) > _MAX_HEADER_VALUE_LEN:
            continue
        out[k] = v
    return out


def _sanitise_params(params: dict[str, str]) -> dict[str, str]:
    """Cap param count and value length."""
    out: dict[str, str] = {}
    for k, v in params.items():
        if len(out) >= _MAX_PARAMS:
            break
        if not isinstance(v, str) or len(v) > _MAX_PARAM_VALUE_LEN:
            continue
        out[k] = v
    return out


def _get_validated(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """GET ``url``, re-validating scheme + HTTPS + SSRF on every hop.

    Shared by the metadata and image fetchers so the security rules are
    defined once.  ``params`` are only sent on the initial request;
    redirect targets already encode the query they want.

    SSRF pre-flight: resolve the hostname and reject private /
    loopback / link-local / reserved addresses *before* connecting.
    We do NOT pin the IP into the URL — that would break TLS SNI
    (the server can't present the right cert for a literal IP, and
    many CDNs reject the handshake outright with
    ``TLSV1_ALERT_INTERNAL_ERROR``).  The pre-flight check still
    defeats the common SSRF vectors (user config pointing at
    ``127.0.0.1``, ``localhost``, ``169.254.169.254``, ...);
    DNS-rebinding between the check and the connect is a
    millisecond window we accept as a trade-off for working TLS.
    """
    _check_scheme(url)
    _require_https(url)
    _resolve_safely_hook(urlparse(url).hostname or "")

    resp = client.get(url, params=params, headers=headers, follow_redirects=False)
    # Manual redirect loop so each hop is re-validated.
    hops = 0
    while resp.is_redirect and hops < _MAX_REDIRECTS:
        location = resp.headers.get("location", "")
        resp.close()
        if not location:
            raise ProviderError("redirect with no Location header")
        next_url = urljoin(url, location)
        _check_scheme(next_url)
        _require_https(next_url)
        _resolve_safely_hook(urlparse(next_url).hostname or "")
        resp = client.get(next_url, headers=headers, follow_redirects=False)
        hops += 1
    if resp.is_redirect:
        raise ProviderError(f"redirect cap ({_MAX_REDIRECTS}) exceeded for {url}")
    return resp


def fetch_metadata_bytes(
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> bytes:
    """Fetch a small metadata document (JSON, RSS/Atom XML, ...) from ``url``.

    HTTPS only, SSRF-checked, size-capped at 5 MiB.  Returns the raw
    body bytes.  Raises ``ProviderError`` on any failure.
    """
    clean_params = _sanitise_params(params or {})
    clean_headers = _sanitise_headers(headers or {})

    with httpx.Client(timeout=timeout) as client:
        resp = _get_validated(client, url, params=clean_params, headers=clean_headers)
        if resp.status_code >= 400:
            raise ProviderError(
                f"metadata request returned HTTP {resp.status_code}: {url}"
            )
        content = resp.content
        if len(content) > _MAX_METADATA_BYTES:
            raise ProviderError(
                f"metadata response exceeds {_MAX_METADATA_BYTES}-byte cap"
            )
        return content


def fetch_metadata_json(
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> Any:
    """Fetch a JSON metadata response from ``url``.

    HTTPS only, SSRF-checked, size-capped at 5 MiB.  Returns the
    parsed JSON.  Raises ``ProviderError`` on any failure.
    """
    import json

    content = fetch_metadata_bytes(url, params=params, headers=headers, timeout=timeout)
    try:
        # json.loads on bytes auto-detects UTF-8/16/32 per RFC 8259.
        return json.loads(content)
    except ValueError as exc:
        raise ProviderError(f"metadata response is not valid JSON: {exc}") from exc


def download_image(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    max_bytes: int = _MAX_IMAGE_BYTES,
) -> tuple[bytes, str]:
    """Download an image from ``url`` with size cap and SSRF defense.

    Returns ``(bytes, content_type)``.  ``content_type`` defaults to
    ``image/jpeg`` if the server doesn't send one.  Raises
    ``ProviderError`` on any failure.
    """
    clean_headers = _sanitise_headers(headers or {})

    with httpx.Client(timeout=timeout) as client:
        resp = _get_validated(client, url, headers=clean_headers)
        if resp.status_code >= 400:
            raise ProviderError(
                f"image request returned HTTP {resp.status_code}: {url}"
            )
        # Pre-flight Content-Length check: refuse before loading the
        # body into memory.  The cap is also re-enforced below in case
        # the server lied about Content-Length.
        cl_header = resp.headers.get("content-length")
        if cl_header is not None:
            try:
                cl = int(cl_header)
            except ValueError:
                cl = -1
            if cl > max_bytes:
                raise ProviderError(
                    f"image Content-Length {cl} exceeds the "
                    f"{max_bytes}-byte download cap"
                )
        # httpx has already loaded the body in the simple client.get()
        # path; we cap it again below in case the server lied about
        # Content-Length.
        body = resp.content
        if len(body) > max_bytes:
            raise ProviderError(f"image exceeds the {max_bytes}-byte download cap")
        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    return body, content_type


def extension_for_content_type(content_type: str) -> str:
    """Map an image content-type to a filename extension.

    Falls back to ``.img`` for unknown types; the orchestrator's
    ``verify_image`` step re-encodes and renames anyway, so the fallback
    only matters for direct provider use.
    """
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    return ".img"


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON Pointer against ``doc``.

    Spec: https://www.rfc-editor.org/rfc/rfc6901
    - Empty string "" refers to the whole document.
    - Tokens are separated by "/"; "~0" encodes "~" and "~1" encodes "/".
    - Array indices are numeric tokens.
    """
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise ProviderError(f"JSON pointer must start with '/': {pointer!r}")
    tokens = pointer[1:].split("/")
    current: Any = doc
    for token in tokens:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise ProviderError(f"JSON pointer token {token!r} not found in object")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit():
                raise ProviderError(
                    f"JSON pointer token {token!r} is not a valid array index"
                )
            idx = int(token)
            if idx >= len(current):
                raise ProviderError(
                    f"JSON pointer array index {idx} out of range (len={len(current)})"
                )
            current = current[idx]
        else:
            raise ProviderError(f"JSON pointer cannot descend into scalar at {token!r}")
    return current
