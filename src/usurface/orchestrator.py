"""High-level orchestration: provider fetch + backend writes.

Pulled out of the CLI so it's testable and reusable by the systemd
service.
"""

from __future__ import annotations

import os
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
from usurface.paths import invoking_user_uid_gid
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


def _restore_shared_owner(path: Path) -> None:
    """If running via sudo, chown ``path`` back to the invoking user.

    The shared wallpaper file must stay writable by the user-mode systemd
    timer even after a one-off ``sudo usurface apply`` run.
    """
    uid_gid = invoking_user_uid_gid()
    if uid_gid is None:
        return
    uid, gid = uid_gid
    try:
        os.chown(path, uid, gid)
    except OSError:
        _log.debug("shared_owner_restore_failed", path=str(path))


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
    adopt_drift: bool = False,
) -> list[str]:
    """Run the apply pipeline for ``config``.

    Returns a list of human-readable lines describing what was done.

    Errors from individual backends are caught and reported as warnings
    so that one failing surface (e.g. SDDM when not run as root) does
    not prevent the other surfaces from being updated. Unexpected
    exceptions still propagate.

    ``adopt_drift``: when True, a :class:`drift.DriftError` for a QML
    file is handled by adopting the drifted (stripped) content as the
    new pristine baseline and proceeding to patch — the explicit consent
    path for after a Plasma update. Without the flag, drifted files are
    skipped with a remediation hint.
    """
    expanded = expand_behaviour_paths(config)
    user_dir = Path(expanded.surface.behaviour.user_dir).expanduser()
    shared_dir = Path(expanded.surface.behaviour.shared_dir)
    user_dir.mkdir(parents=True, exist_ok=True)
    shared_dir.mkdir(parents=True, exist_ok=True)

    # Pre-flight: if the shared directory is not writable, the shared
    # wallpaper copy will fail after we have already downloaded the
    # image. Surface the problem early with a clear, actionable error.
    if not os.access(shared_dir, os.W_OK):
        raise BackendError(
            f"shared wallpaper directory {shared_dir} is not writable",
            hint=(
                "the directory must be writable by the user running usurface. "
                "If you previously ran with sudo, fix ownership with:\n"
                f"  sudo chown -R $USER:$USER {shared_dir}\n"
                "Or change surface.behaviour.shared_dir to a user-writable path."
            ),
        )

    fetched = fetch_wallpaper(expanded)
    clean_bytes = verify_image(fetched.data)
    # verify_image re-encodes to JPEG (or PNG for transparent input), so the
    # output extension must match the actual bytes, not the provider's
    # original suggestion (e.g. a WebP source would otherwise get a .webp
    # filename containing JPEG data).
    ext = ".png" if clean_bytes.startswith(b"\x89PNG\r\n\x1a\n") else ".jpg"

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
        # If we are running via sudo, the atomic replace created the shared
        # file as root. Restore ownership to the invoking user so the daily
        # user-mode systemd timer can overwrite it tomorrow.
        _restore_shared_owner(shared)
        _restore_shared_owner(shared_dir)


    for backend in backends if backends is not None else default_backends(
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
                if adopt_drift:
                    # Explicit consent: adopt the drifted (stripped)
                    # content as the new pristine baseline, then patch.
                    from usurface.theme import extract as _extract

                    vtext = vendor_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    stripped = drift.strip_sentinels(vtext).encode("utf-8")
                    _extract.copy_pristine_bytes(name, stripped)
                    _log.warning(
                        "qml_drift_adopted",
                        backend=name,
                        backup=str(exc.backup_path),
                    )
                    plan.append(
                        f"QML backend '{name}' DRIFT ADOPTED: {exc.backup_path}"
                    )
                    # Re-run the patch with the new baseline.
                    msg = apply_font_tokens(
                        name=name,
                        vendor_path=vendor_path,
                        manifest=manifest,
                        patch=font_patch,
                    )
                    plan.append(f"QML backend '{name}' applied: {msg}")
                    if name == "plasma_lockscreen_ui":
                        lmsg = apply_lock_tokens(
                            name=name,
                            vendor_path=vendor_path,
                            manifest=manifest,
                            patch=lock_patch,
                        )
                        plan.append(f"QML lock '{name}': {lmsg}")
                else:
                    # Drifted vendor file: skip patching but keep going
                    # so other surfaces still apply. The user must
                    # explicitly accept the new vendor content.
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

        # If we are running via sudo, every backend/QML write recorded into
        # the manifest created root-owned entries and snapshots. Restore
        # ownership of the manifest log and the snapshots/state directory
        # to the invoking user *after* all writes + compaction are done,
        # so the daily user-mode systemd timer can keep appending.
        _restore_shared_owner(manifest.path)
        _restore_shared_owner(manifest.path.parent)

    return plan
