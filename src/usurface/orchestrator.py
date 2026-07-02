"""High-level orchestration: provider fetch + backend writes.

Pulled out of the CLI so it's testable and reusable by the systemd
service.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from usurface.backends.base import Backend, BackendError
from usurface.backends.desktop import DesktopBackend
from usurface.backends.lock import LockBackend
from usurface.backends.login import LoginBackend
from usurface.config import Config, expand_behaviour_paths
from usurface.logging import get_logger
from usurface.manifest import Manifest, write_tracked
from usurface.providers import (
    FetchedImage,
    ProviderError,
    fetch_from_source,
    make_plugin_manager,
)

_log = get_logger(__name__)


def default_backends(*, accent_color: str | None = None) -> list[Backend]:
    """Return the default list of backends in apply order.

    ``accent_color`` (from ``config.surface.login.accent_color``) is
    forwarded to the login backend so it can write the SDDM theme.conf
    ``color=`` key.
    """
    return [DesktopBackend(), LockBackend(), LoginBackend(accent_color=accent_color)]


def verify_image(data: bytes) -> bytes:
    """Decode ``data`` with Pillow to confirm it is a valid image.

    Strips EXIF metadata as a small privacy/security improvement.
    Returns the (possibly re-encoded) JPEG bytes suitable for use as a
    wallpaper.
    """
    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            # Re-encode to strip any non-JPEG/PNG metadata. Choose format
            # based on input to preserve transparency for PNG.
            out = BytesIO()
            fmt = "PNG" if img.format == "PNG" else "JPEG"
            save_kwargs: dict[str, object] = {"optimize": True}
            if fmt == "JPEG":
                save_kwargs["quality"] = 90
            img.save(out, format=fmt, **save_kwargs)
            return out.getvalue()
    except UnidentifiedImageError as exc:
        raise ProviderError(f"downloaded data is not a valid image: {exc}") from exc


def fetch_wallpaper(config: Config) -> FetchedImage:
    """Resolve the configured source to a :class:`FetchedImage`."""
    pm = make_plugin_manager()
    return fetch_from_source(pm, config.surface.source)


def apply_to_surfaces(
    config: Config,
    *,
    manifest: Manifest,
    backends: list[Backend] | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Run the apply pipeline for ``config``.

    Returns a list of human-readable lines describing what was done.

    Errors from individual backends are caught and reported as warnings
    so that one failing surface (e.g. SDDM when not run as root) does
    not prevent the other surfaces from being updated. Unexpected
    exceptions still propagate.
    """
    expanded = expand_behaviour_paths(config)
    user_dir = Path(expanded.surface.behaviour.user_dir).expanduser()
    shared_dir = Path(expanded.surface.behaviour.shared_dir)
    user_dir.mkdir(parents=True, exist_ok=True)
    shared_dir.mkdir(parents=True, exist_ok=True)

    fetched = fetch_wallpaper(expanded)
    clean_bytes = verify_image(fetched.data)
    ext = fetched.suggested_extension or ".jpg"

    canonical = user_dir / f"last_wallpaper{ext}"
    shared = shared_dir / f"last_wallpaper{ext}"

    plan: list[str] = []

    from usurface.theme import extract

    if dry_run:
        plan.append(f"fetch from provider '{expanded.surface.source.provider}'")
        plan.append("verify image (decode + re-encode)")
        plan.append(f"write {canonical}")
        plan.append(f"copy to {shared} (mode 0644)")
    else:
        # Atomic writes with manifest tracking.
        write_tracked(manifest, canonical, clean_bytes, mode=0o644)
        plan.append(f"wrote {canonical} ({len(clean_bytes)} bytes)")
        write_tracked(manifest, shared, clean_bytes, mode=0o644)
        plan.append(f"wrote {shared} (mode 0644)")

    for backend in backends or default_backends(
        accent_color=expanded.surface.login.accent_color
    ):
        if dry_run:
            plan.extend(backend.dry_run_plan(shared))
        else:
            try:
                backend.apply(manifest, shared)
                plan.append(f"backend '{backend.name}' applied")
            except BackendError as exc:
                plan.append(f"backend '{backend.name}' FAILED: {exc}")
                if exc.hint:
                    plan.append(f"  hint: {exc.hint}")
                _log.warning("backend_failed", backend=backend.name, error=str(exc))

    # QML Patching
    if dry_run:
        for name, vendor_path in extract.DEFAULT_TARGETS:
            if vendor_path.is_file():
                plan.append(f"patch QML {name} ({vendor_path}) with font/theme tokens")
    else:
        from usurface.theme.qml_patch import (
            FontPatch,
            LockPatch,
            apply_font_tokens,
            apply_lock_tokens,
        )
        from usurface.theme import drift

        font_patch = FontPatch(
            family=expanded.surface.fonts.family,
            weight=expanded.surface.fonts.weight,
            password_character=expanded.surface.fonts.password_character,
            clock_format=expanded.surface.login.clock_format,
        )
        lock_patch = LockPatch(
            on_idle_dim_seconds=expanded.surface.lock.on_idle_dim_seconds,
            suppress_wake_keypress=expanded.surface.lock.suppress_wake_keypress,
        )

        for name, vendor_path in extract.DEFAULT_TARGETS:
            if not vendor_path.is_file():
                continue
            try:
                # Handle template drift if any
                drift.handle_drift(name, vendor_path)
                # Patch font tokens on all targets.
                msg = apply_font_tokens(
                    name=name,
                    vendor_path=vendor_path,
                    manifest=manifest,
                    patch=font_patch,
                )
                plan.append(f"QML backend '{name}' applied: {msg}")
                # Patch lock-specific structural tokens on lockscreen
                # targets only (the fadeoutTimer lives in
                # LockScreenUi.qml).
                if name == "plasma_lockscreen_ui":
                    lmsg = apply_lock_tokens(
                        name=name,
                        vendor_path=vendor_path,
                        manifest=manifest,
                        patch=lock_patch,
                    )
                    plan.append(f"QML lock '{name}': {lmsg}")
            except drift.DriftError as exc:
                # Drifted vendor file: skip patching but keep going so
                # other surfaces still apply. The user must explicitly
                # accept the new vendor content (qml-update-templates
                # or apply --adopt-drift).
                plan.append(f"QML backend '{name}' DRIFTED: {exc}")
                plan.append(
                    "  hint: run `usurface qml-update-templates` "
                    "to accept the new vendor content"
                )
                _log.warning(
                    "qml_backend_drift",
                    backend=name,
                    error=str(exc),
                )
            except BackendError as exc:
                plan.append(f"QML backend '{name}' FAILED: {exc}")
                if exc.hint:
                    plan.append(f"  hint: {exc.hint}")
                _log.warning("qml_backend_failed", backend=name, error=str(exc))
            except OSError as exc:
                # Drift backup creation or atomic write failed (likely
                # permission). Treat as a backend failure.
                msg = f"{vendor_path}: {exc}"
                hint = (
                    "QML patching needs to write to system paths. "
                    "Re-run with sudo, e.g.  sudo usurface apply"
                )
                plan.append(f"QML backend '{name}' FAILED: {msg}")
                plan.append(f"  hint: {hint}")
                _log.warning(
                    "qml_backend_failed_oserror",
                    backend=name,
                    error=msg,
                )

    # Bound undo history: compact the manifest to the most recent
    # retention threshold entries and prune orphaned snapshots. Only
    # runs on a real apply (not dry-run) and only if the pipeline
    # reached this point without raising.
    if not dry_run:
        from usurface.manifest import compact

        dropped = compact(manifest)
        if dropped:
            plan.append(f"compacted manifest (dropped {dropped} old entries)")

    return plan
