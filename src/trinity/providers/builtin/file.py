"""Local-file provider.

Expects the option ``path``. Returns the file's bytes verbatim.

The path must resolve to one of a small set of safe roots: the user's
``~/Pictures`` and ``~/Wallpapers`` directories, the default system
wallpaper locations (``/usr/share/wallpapers``,
``/usr/share/backgrounds``, ``/usr/local/share/wallpapers``), and the
plasmalogin-visible shared directory (``TRINITY_SHARED_DIR`` if set,
else ``/usr/local/share/wallpapers``). Any other path is rejected
before being read; this prevents a malicious config from pulling in
arbitrary local files (e.g. ``/etc/shadow``) and the orchestrator from
re-encoding them as wallpapers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trinity.providers import FetchedImage, ProviderError

# Refuse to read a local image larger than this into memory before
# decoding/re-encoding. The orchestrator's verify_image step decodes the
# full image with Pillow, so an enormous local file (e.g. a 2 GB TIFF)
# could exhaust memory on a low-RAM laptop. 100 MiB is generous for any
# real wallpaper and still bounds the worst case.
_MAX_LOCAL_BYTES = 100 * 1024 * 1024


class FileOptions(BaseModel):
    """Validated options for the local-file provider."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(
        description="Path to a local image file (expanded with ~ and $VAR).",
        min_length=1,
    )


# Defaults used when fetch() is called directly (not via the schema-validated
# pipeline). The schema in FileOptions is the source of truth for validation;
# these are just the no-args defaults so direct tests work.
_DEFAULT_OPTIONS: dict[str, Any] = {}


# Allow-listed path roots for the ``file`` provider. A user-supplied
# path must resolve (after ``~``/``$VAR`` expansion and symlink
# resolution) to be inside one of these directories. The shared
# plasmalogin-visible dir is added at runtime if it exists, so a
# wallpaper already in place there can be re-applied without copying
# it into ``~/Pictures`` first.
_ALLOWED_ROOT_TEMPLATES: tuple[str, ...] = (
    "~/Pictures",
    "~/Wallpapers",
    "/usr/share/wallpapers",
    "/usr/share/backgrounds",
    "/usr/local/share/wallpapers",
)


def _allowed_roots() -> tuple[Path, ...]:
    """Expand the allow-list templates against the *current* environment.

    Expanded per-call (not at import time) so a changed ``HOME`` — a
    ``sudo`` run, or test isolation — is honoured.
    """
    return tuple(Path(r).expanduser() for r in _ALLOWED_ROOT_TEMPLATES)


def _resolved_allowed_roots() -> tuple[Path, ...]:
    """Allowed roots, including the runtime shared wallpaper dir.

    Resolved per-call so changes to ``TRINITY_SHARED_DIR`` are honoured.
    Roots that don't exist on the host are kept anyway — the check is
    a path-prefix containment, not an existence check.
    """
    roots: list[Path] = list(_allowed_roots())
    shared = os.environ.get("TRINITY_SHARED_DIR")
    if shared:
        try:
            p = Path(shared).expanduser().resolve()
        except (OSError, RuntimeError):
            p = Path(shared).expanduser()
        if p not in roots:
            roots.append(p)
    return tuple(roots)


def _is_under(path: Path, roots: tuple[Path, ...]) -> bool:
    """Return True if ``path`` is contained in any of ``roots``."""
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    for root in roots:
        try:
            rroot = root.resolve(strict=False)
        except (OSError, RuntimeError):
            rroot = root
        try:
            resolved.relative_to(rroot)
            return True
        except ValueError:
            continue
    return False


def fetch(options: dict[str, Any]) -> FetchedImage:
    opts = {**_DEFAULT_OPTIONS, **options}
    raw = opts.get("path")
    if not isinstance(raw, str) or not raw:
        raise ProviderError("file provider requires a 'path' option")

    path = Path(os.path.expandvars(os.path.expanduser(raw)))

    # Security: refuse paths that don't resolve to an allow-listed root.
    # A symlink that escapes the root is also caught here because we
    # compare against ``path.resolve()``. Checked *before* the existence
    # check so the error message never discloses whether a path outside
    # the allowed roots exists.
    if not _is_under(path, _resolved_allowed_roots()):
        allowed = ", ".join(str(r) for r in _allowed_roots())
        raise ProviderError(
            f"file provider: path {path} is not under an allowed root "
            f"({allowed}). Set TRINITY_SHARED_DIR or move the image into "
            f"an allowed directory."
        )

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
