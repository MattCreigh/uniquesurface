"""High-level orchestration: provider fetch + backend writes.

Pulled out of the CLI so it's testable and reusable by the systemd
service.
"""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from trinity import refresh_state
from trinity.backends.base import Backend, BackendError
from trinity.backends.desktop import DesktopBackend
from trinity.backends.lock import LockBackend
from trinity.backends.login import LoginBackend
from trinity.config import Config, expand_behaviour_paths
from trinity.logging_setup import get_logger
from trinity.manifest import Manifest, sha256_bytes, write_tracked
from trinity.paths import invoking_user_uid_gid
from trinity.providers import (
    FetchedImage,
    ProviderError,
    fetch_from_source,
    make_plugin_manager,
    probe_from_source,
)

_log = get_logger(__name__)

# Logical template names of the lock-screen QML files that receive the
# structural lock-token edits (see LockPatch in theme.qml_patch).
_LOCKSCREEN_QML_NAMES = frozenset(
    {"plasma_lockscreen_ui", "plasma_lockscreen_mainblock"}
)


def default_backends(*, accent_color: str | None = None) -> list[Backend]:
    """Return the default list of backends in apply order.

    ``accent_color`` (from ``config.surface.login.accent_color``) is
    forwarded to the login backend so it can write the SDDM theme.conf
    ``color=`` key.
    """
    return [DesktopBackend(), LockBackend(), LoginBackend(accent_color=accent_color)]


def _has_non_default_token_values(config: Config) -> bool:
    """Return True if any font/lock/login token differs from its default.

    Used to warn when theme_tokens is disabled but the user has set
    non-default token values — they'd be silently ignored.
    """
    from trinity.schema import Fonts, Lock, Login

    default_fonts = Fonts()
    default_login = Login()
    default_lock = Lock()
    surface = config.surface
    if surface.fonts != default_fonts:
        return True
    if surface.login != default_login:
        return True
    if surface.lock != default_lock:
        return True
    return False


def _restore_shared_owner(path: Path) -> None:
    """If running via sudo, chown ``path`` back to the invoking user.

    The shared wallpaper file must stay writable by the user-mode systemd
    timer even after a one-off ``sudo trinity apply`` run.
    """
    uid_gid = invoking_user_uid_gid()
    if uid_gid is None:
        return
    uid, gid = uid_gid
    try:
        os.chown(path, uid, gid)
    except OSError:
        _log.debug("shared_owner_restore_failed", path=str(path))


def _display_manager_name() -> str | None:
    """Return the name of the active display manager unit, or None.

    Used to tell the user which service to restart to see the new login
    wallpaper.  When the user passes ``--restart-dm`` we will *also*
    invoke :func:`_restart_display_manager` on the unit returned here.

    We probe the SDDM/plasmalogin/display-manager aliases in that order
    so the returned name is the one ``systemctl restart`` will accept
    on a Neon-style install.
    """
    import shutil
    import subprocess

    systemctl = shutil.which("systemctl")
    if not systemctl:
        return None
    for unit in ("plasmalogin", "sddm", "display-manager"):
        probe = subprocess.run(
            [systemctl, "is-active", "--quiet", unit],
            check=False,
            capture_output=True,
            timeout=5.0,
        )
        if probe.returncode == 0:
            return unit
    return None


def _have_pkexec() -> bool:
    """Return True if a privilege-elevation tool we can drive is on PATH.

    Used to decide whether the ``--restart-dm`` opt-in can actually
    fire when the user is not already root.  Returns False when the
    tool is missing — the user gets a clear hint about running with
    sudo instead of a silent no-op.
    """
    import shutil

    return shutil.which("pkexec") is not None or shutil.which("sudo") is not None


def _restart_display_manager(unit: str, plan: list[str]) -> None:
    """Restart ``unit`` via systemctl.  Caller has gated the privilege.

    Never raises on unit-not-found: the user just gets a hint.  On a
    real restart failure we still continue (the wallpaper is already
    written) and surface the error in the plan.

    This terminates the user's running Wayland session, which is why
    it is gated by the ``--restart-dm`` flag and a privilege check.
    """
    import shutil
    import subprocess

    systemctl = shutil.which("systemctl")
    if systemctl is None:
        plan.append("  systemctl not on PATH; cannot auto-restart")
        return
    # When run as root, invoke systemctl directly.  When run as a
    # regular user, prefer sudo -n (non-interactive).  The caller has
    # already checked that the user has *some* escalation path; we
    # use the non-interactive form so the command cannot hang waiting
    # for a password prompt in the middle of an apply.
    if os.geteuid() == 0:
        argv = [systemctl, "restart", unit]
    else:
        sudo = shutil.which("sudo")
        if sudo is None:
            plan.append("  sudo not on PATH; cannot auto-restart")
            return
        argv = [sudo, "-n", systemctl, "restart", unit]
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            timeout=15.0,
        )
    except subprocess.TimeoutExpired:
        plan.append(f"  systemctl restart {unit} timed out (>15s)")
        return
    except OSError as exc:
        plan.append(f"  systemctl restart {unit} failed: {exc}")
        return
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", "replace").strip()
        plan.append(f"  systemctl restart {unit} returned {proc.returncode}: {stderr}")
        _log.warning(
            "display_manager_restart_failed",
            unit=unit,
            returncode=proc.returncode,
            stderr=stderr,
        )
        return
    plan.append(f"  {unit} restarted successfully; greeter is reloading")


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
            # based on the input mode: preserve transparency for images
            # with an alpha channel; flatten everything else to JPEG
            # (which does not support alpha and would raise on save).
            has_alpha = img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            )
            if has_alpha:
                fmt = "PNG"
                save_kwargs: dict[str, object] = {"optimize": True}
            else:
                fmt = "JPEG"
                if img.mode != "RGB":
                    img = img.convert("RGB")
                save_kwargs = {"optimize": True, "quality": 90}
            out = BytesIO()
            img.save(out, format=fmt, **save_kwargs)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ProviderError(f"downloaded data is not a valid image: {exc}") from exc


def fetch_wallpaper(config: Config, pm: Any = None) -> FetchedImage:
    """Resolve the configured source to a :class:`FetchedImage`."""
    if pm is None:
        pm = make_plugin_manager()
    return fetch_from_source(pm, config.surface.source)


def _safe_probe(pm: Any, source: Any) -> str | None:
    """Ask the provider for its change token; never raise.

    Fail open: a broken or unsupported probe degrades ``--if-changed``
    to a full fetch — it must never stop the wallpaper refreshing.
    """
    try:
        return probe_from_source(pm, source)
    except ProviderError as exc:
        _log.warning("probe_failed", provider=source.provider, error=str(exc))
        return None
    except Exception as exc:
        # Third-party plugins can raise anything; contain it here.
        _log.warning(
            "probe_failed_unexpected", provider=source.provider, error=str(exc)
        )
        return None


def _prune_stale_wallpapers(directory: Path, *, keep: set[Path]) -> list[Path]:
    """Remove old content-hash-named wallpaper files from ``directory``.

    Keeps everything in ``keep`` (the file just written and the stable
    alias), skips symlinks (the alias when it is a symlink), and keeps
    the single most recent predecessor: a consumer may still reference
    the previous file until its backend runs again, and deleting it
    would blank that surface.  Everything older is removed so the
    hash-named files can't accumulate.
    """
    candidates: list[tuple[float, Path]] = []
    for path in directory.glob("last_wallpaper*"):
        if path in keep or path.suffix not in (".jpg", ".png"):
            continue
        try:
            if path.is_symlink() or not path.is_file():
                continue
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    candidates.sort(reverse=True)
    removed: list[Path] = []
    for _mtime, path in candidates[1:]:
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            _log.debug("stale_wallpaper_unlink_failed", path=str(path))
    return removed


def _update_stable_alias(alias: Path, *, target: Path, plan: list[str]) -> None:
    """Point ``alias`` (``last_wallpaper.jpg``) at the current generation.

    Atomic: a temp symlink is renamed over the alias, so readers never
    see a missing path — this also transparently migrates the fixed-name
    regular file that pre-content-addressing versions wrote.  Falls back
    to a plain copy on filesystems without symlink support so the stable
    path always resolves.
    """
    tmp = alias.with_name(alias.name + ".tmp")
    try:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        os.symlink(target.name, tmp)
        os.replace(tmp, alias)
        plan.append(f"stable alias {alias} -> {target.name}")
        return
    except OSError:
        pass
    try:
        from trinity.atomic import atomic_write_bytes

        atomic_write_bytes(alias, target.read_bytes(), mode=0o644)
        _restore_shared_owner(alias)
        plan.append(f"stable alias {alias} (copy; symlinks unsupported)")
    except OSError as exc:
        plan.append(f"stable alias {alias} FAILED: {exc}")
        _log.warning("stable_alias_failed", alias=str(alias), error=str(exc))


def apply_to_surfaces(
    config: Config,
    *,
    manifest: Manifest,
    backends: list[Backend] | None = None,
    dry_run: bool = False,
    adopt_drift: bool = False,
    restart_dm: bool = False,
    if_changed: bool = False,
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

    ``restart_dm``: when True, AND the login surface was actually
    changed, AND the user is running with sufficient privilege (root,
    or via sudo/PKEXEC), AND the user passes the explicit ``--restart-dm``
    CLI flag, restart the detected display manager so the new SDDM
    wallpaper takes effect immediately.  This terminates the current
    Wayland session — opt-in only, gated by the CLI flag, never
    automatic.  When False (the default) trinity prints a clear hint
    and leaves the restart to the user.

    ``if_changed``: when True, first ask the provider for a cheap
    change token (see ``trinity_provider_probe``) and skip the whole
    pipeline when it matches the persisted state from the last apply.
    Providers without a probe fall back to a full fetch, and the
    (verified) image digest is compared instead — the surfaces are
    only rewritten when the image bytes actually changed.  The change
    unit is "new image on disk": run a plain ``apply`` to force
    surface writes after e.g. fixing backend permissions.  Used by the
    hourly systemd timer so upstream publishes (which happen at
    provider-specific times) land within the hour without hammering
    the provider with image downloads.
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
                "the directory must be writable by the user running trinity. "
                "If you previously ran with sudo, fix ownership with:\n"
                f"  sudo chown -R $USER:$USER {shared_dir}\n"
                "Or change surface.behaviour.shared_dir to a user-writable path."
            ),
        )

    pm = make_plugin_manager()
    state_file = user_dir / refresh_state.STATE_FILENAME
    prior_state = refresh_state.load(state_file)
    fingerprint = refresh_state.source_fingerprint(
        expanded.surface.source.provider,
        expanded.surface.source.options.model_dump(),
    )
    if prior_state is not None and prior_state.fingerprint != fingerprint:
        # Config changed since the last apply: none of the cached
        # token/digest comparisons are meaningful any more.
        prior_state = None

    probe_token: str | None = None
    if if_changed and not dry_run:
        probe_token = _safe_probe(pm, expanded.surface.source)
        if (
            prior_state is not None
            and probe_token is not None
            and prior_state.probe_token == probe_token
            and Path(prior_state.wallpaper_path).is_file()
        ):
            _log.info(
                "refresh_skipped_unchanged",
                provider=expanded.surface.source.provider,
                token=probe_token,
            )
            return ["source unchanged (provider change token matches); nothing to do"]

    fetched = fetch_wallpaper(expanded, pm)
    clean_bytes = verify_image(fetched.data)
    # verify_image re-encodes to JPEG (or PNG for transparent input), so the
    # output extension must match the actual bytes, not the provider's
    # original suggestion (e.g. a WebP source would otherwise get a .webp
    # filename containing JPEG data).
    ext = ".png" if clean_bytes.startswith(b"\x89PNG\r\n\x1a\n") else ".jpg"
    image_sha = sha256_bytes(clean_bytes)

    # The filename carries a digest of the content: Plasma's org.kde.image
    # plugin doesn't watch file contents and KConfig only emits a change
    # signal when the Image= *value* changes, so overwriting a fixed
    # filename updates the bytes on disk but never repaints the running
    # shell. A content-addressed name makes every new image a new URI,
    # which all surfaces react to.
    stem = f"last_wallpaper-{image_sha[:12]}"
    canonical = user_dir / f"{stem}{ext}"
    shared = shared_dir / f"{stem}{ext}"
    # Stable alias for consumers that resolve the path at read time.
    # SDDM re-reads theme.conf.user + the image at every greeter start,
    # so it wants a *fixed* path: the user-mode timer usually cannot
    # rewrite theme.conf.user (root-owned), and a hash-named target
    # would eventually be pruned underneath it. The symlink always
    # points at the current generation.
    shared_stable = shared_dir / f"last_wallpaper{ext}"

    if (
        if_changed
        and not dry_run
        and prior_state is not None
        and prior_state.image_sha256 == image_sha
        and shared.is_file()
    ):
        # Same image bytes as the last apply (providers without a probe
        # land here after a full fetch). Refresh the stored token so the
        # next run can skip the image download too, and leave the
        # surfaces alone.
        refresh_state.save(
            state_file,
            refresh_state.RefreshState(
                fingerprint=fingerprint,
                probe_token=probe_token,
                image_sha256=image_sha,
                wallpaper_path=str(shared),
                applied_at=refresh_state.now_iso(),
            ),
        )
        _restore_shared_owner(state_file)
        _log.info(
            "refresh_skipped_same_image",
            provider=expanded.surface.source.provider,
            sha256=image_sha,
        )
        return ["wallpaper unchanged (image digest matches); surfaces not touched"]

    plan: list[str] = []

    from trinity.theme import extract

    if dry_run:
        plan.append(f"fetch from provider '{expanded.surface.source.provider}'")
        plan.append("verify image (decode + re-encode)")
        plan.append(f"write {canonical}")
        plan.append(f"copy to {shared} (mode 0644)")
        plan.append(f"point stable alias {shared_stable} at {shared.name}")
    else:
        # Atomic writes with manifest tracking.
        write_tracked(manifest, canonical, clean_bytes, mode=0o644)
        plan.append(f"wrote {canonical} ({len(clean_bytes)} bytes)")
        write_tracked(manifest, shared, clean_bytes, mode=0o644)
        plan.append(f"wrote {shared} (mode 0644)")
        # If we are running via sudo, the atomic replace created the shared
        # wallpaper file as root. Restore ownership of the *file* to the
        # invoking user so the user-mode systemd timer can overwrite it on
        # the next refresh. We deliberately do NOT chown the directory
        # itself — it should stay root-owned + world-readable so SDDM
        # (running as a system user) can read the wallpaper.
        _restore_shared_owner(shared)
        _update_stable_alias(shared_stable, target=shared, plan=plan)
        # Content-addressed names change on every new image; drop old
        # generations (keeping the newest predecessor, see
        # _prune_stale_wallpapers) so they can't accumulate.
        for old in _prune_stale_wallpapers(
            user_dir, keep={canonical}
        ) + _prune_stale_wallpapers(shared_dir, keep={shared, shared_stable}):
            plan.append(f"removed stale wallpaper {old}")
        # Record what this apply produced. The new image is on disk at
        # this point; the surface backends below all point at it and are
        # individually best-effort. --if-changed compares against this
        # state on the next run.
        refresh_state.save(
            state_file,
            refresh_state.RefreshState(
                fingerprint=fingerprint,
                probe_token=probe_token,
                image_sha256=image_sha,
                wallpaper_path=str(shared),
                applied_at=refresh_state.now_iso(),
            ),
        )
        _restore_shared_owner(state_file)

    login_applied = False
    for backend in (
        backends
        if backends is not None
        else default_backends(accent_color=expanded.surface.login.accent_color)
    ):
        # Desktop/lock get the content-addressed path: Plasma caches by
        # URI, so only a changing value forces a repaint. Login gets the
        # stable alias: SDDM resolves the path at every greeter start,
        # and theme.conf.user is usually not rewritable by the user-mode
        # timer (root-owned) — the alias keeps it pointing at the
        # current image without needing a rewrite.
        backend_target = shared_stable if backend.name == "login" else shared
        if dry_run:
            plan.extend(backend.dry_run_plan(backend_target))
        else:
            try:
                backend.apply(manifest, backend_target)
                plan.append(f"backend '{backend.name}' applied")
                if backend.name == "login":
                    login_applied = True
            except BackendError as exc:
                plan.append(f"backend '{backend.name}' FAILED: {exc}")
                if exc.hint:
                    plan.append(f"  hint: {exc.hint}")
                _log.warning("backend_failed", backend=backend.name, error=str(exc))

    # QML Patching (gated by theme_tokens.enabled; opt-in feature).
    if not expanded.surface.theme_tokens.enabled:
        plan.append(
            "theme tokens: disabled "
            "(set [surface.theme_tokens] enabled = true to enable QML patching)"
        )
        if _has_non_default_token_values(expanded):
            _log.warning(
                "theme_tokens_disabled_with_custom_values",
                hint=(
                    "font/lock/login token values are set but theme_tokens is "
                    "disabled; they are ignored. Enable with "
                    "[surface.theme_tokens] enabled = true."
                ),
            )
            plan.append(
                "  warning: font/lock/login token values are set but ignored "
                "while theme_tokens is disabled"
            )
    elif dry_run:
        for name, vendor_path in extract.DEFAULT_TARGETS:
            if vendor_path.is_file():
                plan.append(f"patch QML {name} ({vendor_path}) with font/theme tokens")
    else:
        from trinity.theme import drift
        from trinity.theme.descriptors import (
            detect_plasma_version,
        )
        from trinity.theme.descriptors import (
            select as select_descriptor,
        )
        from trinity.theme.qml_patch import (
            FontPatch,
            LockPatch,
            apply_font_tokens,
            apply_lock_tokens,
        )
        from trinity.theme.qmllint import lint_file as qmllint_lint_file

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

        plasma = detect_plasma_version()
        if not plasma.known:
            plan.append(
                "theme tokens: skipped (Plasma version unknown — "
                "plasmashell --version not on PATH); tokens ignored"
            )
            _log.warning(
                "theme_tokens_plasma_unknown",
                hint=(
                    "Install plasmashell or set $TRINITY_PLASMA_VERSION to a "
                    "PEP 440 version string to enable QML patching."
                ),
            )
            return plan

        for name, vendor_path in extract.DEFAULT_TARGETS:
            if not vendor_path.is_file():
                continue
            descriptor = select_descriptor(name, plasma)
            if descriptor is None:
                plan.append(
                    f"QML backend '{name}': skipped (theme tokens "
                    f"unsupported on Plasma {plasma.version_str})"
                )
                _log.info(
                    "theme_tokens_unsupported",
                    backend=name,
                    plasma=plasma.version_str,
                )
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
                # Patch lock-specific structural tokens. The two edits
                # live in different vendor files:
                #   - ``on_idle_dim_seconds`` rewrites the ``fadeoutTimer``
                #     interval in ``plasma_lockscreen_ui`` (LockScreenUi.qml).
                #   - ``suppress_wake_keypress`` inserts/removes a guard in
                #     the password box's ``Keys.onPressed`` handler in
                #     ``plasma_lockscreen_mainblock`` (MainBlock.qml).
                # ``apply_lock_tokens`` is a no-op on a file whose anchor
                # regex doesn't match, so calling it on both is safe and
                # ensures the right edit lands in the right file.
                if name in _LOCKSCREEN_QML_NAMES:
                    lmsg = apply_lock_tokens(
                        name=name,
                        vendor_path=vendor_path,
                        manifest=manifest,
                        patch=lock_patch,
                    )
                    plan.append(f"QML lock '{name}': {lmsg}")

                # Post-patch qmllint validation: a QML syntax error
                # introduced by a trinity patch would cause the SDDM
                # greeter / lock screen to fall back to the built-in
                # blue locker.  Fail closed: roll the patched bytes
                # back via the manifest and surface the error.
                lint = qmllint_lint_file(vendor_path)
                if not lint.ok:
                    from trinity.theme import extract as _extract

                    pristine = _extract.read_pristine(name)
                    if pristine is not None:
                        write_tracked(manifest, vendor_path, pristine, mode=0o644)
                    plan.append(
                        f"QML backend '{name}' LINT FAILED; reverted to pristine"
                    )
                    if lint.timed_out:
                        plan.append("  qmllint timed out (>5s)")
                    elif lint.stderr.strip():
                        first_line = lint.stderr.strip().splitlines()[0]
                        plan.append(f"  qmllint: {first_line}")
                    _log.warning(
                        "qml_lint_failed",
                        backend=name,
                        stderr=lint.stderr,
                        stdout=lint.stdout,
                    )
            except drift.DriftError as exc:
                if adopt_drift:
                    # Explicit consent: adopt the drifted (stripped)
                    # content as the new pristine baseline, then patch.
                    from trinity.theme import extract as _extract

                    vtext = vendor_path.read_text(encoding="utf-8", errors="replace")
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
                    # Apply lock tokens on lock-screen files, mirroring the
                    # non-adopt path, so the wake-keypress guard also lands
                    # after a drift adoption.
                    if name in _LOCKSCREEN_QML_NAMES:
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
                        "  hint: run `trinity qml-update-templates` "
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
                    "Re-run with sudo, e.g.  sudo trinity apply"
                )
                plan.append(f"QML backend '{name}' FAILED: {msg}")
                plan.append(f"  hint: {hint}")
                _log.warning(
                    "qml_backend_failed_oserror",
                    backend=name,
                    error=msg,
                )

    # If the login (SDDM/plasmalogin) theme config was actually changed,
    # the greeter reads theme.conf + theme.conf.user once at startup
    # and "switch user" reuses the existing greeter, so the new
    # wallpaper is invisible until the DM is restarted.
    #
    # Policy: trinity NEVER restarts the DM automatically — that would
    # terminate the user's running Wayland session without warning.
    # The default behavior is to print a clear hint and let the user
    # restart manually.  The user can opt in to the auto-restart with
    # ``--restart-dm`` on the CLI (which propagates here as the
    # ``restart_dm`` flag).  Even with the flag, the restart only runs
    # if (a) the user has sufficient privilege (uid 0 or sudo) and
    # (b) we can find the DM unit.  Any of those conditions failing
    # falls back to the hint.
    if not dry_run and login_applied:
        dm = _display_manager_name()
        if not dm:
            plan.append(
                "login wallpaper updated; log out fully (not switch-user) "
                "to see the new SDDM wallpaper"
            )
        elif restart_dm and (os.geteuid() == 0 or _have_pkexec()):
            plan.append(
                f"restarting {dm} (--restart-dm) — your current session "
                f"will be terminated"
            )
            _log.warning(
                "display_manager_restart",
                unit=dm,
                trigger="--restart-dm",
            )
            _restart_display_manager(dm, plan)
        else:
            plan.append(
                f"login wallpaper updated; restart {dm} to see it: "
                f"sudo systemctl restart {dm}"
            )
            if restart_dm and os.geteuid() != 0 and not _have_pkexec():
                plan.append(
                    f"  (--restart-dm requested but {os.geteuid()} != 0; "
                    f"run with sudo to enable)"
                )

    # Bound undo history: compact the manifest to the most recent
    # retention threshold entries and prune orphaned snapshots. Only
    # runs on a real apply (not dry-run) and only if the pipeline
    # reached this point without raising.
    if not dry_run:
        from trinity.manifest import compact

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
