"""Helpers around the ``kwriteconfig6`` / ``kreadconfig6`` shell-outs.

We shell out only for files Plasma itself writes (``appletsrc``,
``kscreenlockerrc``). For files we own, we write them ourselves via
:mod:`usurface.atomic`.

All subprocess calls support a ``dry_run`` flag so the planner can
preview what would be written without touching the system.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from usurface.logging import get_logger

_log = get_logger(__name__)


class KConfigToolMissing(RuntimeError):
    """Raised when ``kwriteconfig6`` / ``kreadconfig6`` is not on PATH."""


def ensure_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise KConfigToolMissing(
            f"required tool {name!r} not found on PATH; "
            "this tool is provided by plasma6-kdecoration / plasma-desktop"
        )
    return path


def kwriteconfig(
    *,
    file: Path,
    group: str,
    key: str,
    value: str,
    type_: str | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Call ``kwriteconfig6`` to set a key.

    Returns the argv that was (or would have been) invoked, so it can be
    included in dry-run output.
    """
    argv: list[str] = [
        ensure_tool("kwriteconfig6"),
        "--file",
        str(file),
        "--group",
        group,
        "--key",
        key,
    ]
    if type_:
        argv.extend(["--type", type_])
    argv.append(value)
    if dry_run:
        return argv
    _log.info("kwriteconfig", argv=argv)
    subprocess.run(argv, check=True)
    return argv


def kwriteconfig_nested(
    *,
    file: Path,
    group_path: Sequence[str],
    key: str,
    value: str,
    type_: str | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Call ``kwriteconfig6`` with a nested group path.

    kwriteconfig6 takes ``--group`` once per nesting level; this produces
    ``[A][B][C]`` in the INI file. Returns the argv (for dry-run output).
    """
    if not group_path:
        raise ValueError("group_path must not be empty")
    argv: list[str] = [ensure_tool("kwriteconfig6"), "--file", str(file)]
    for g in group_path:
        argv.extend(["--group", g])
    argv.extend(["--key", key])
    if type_:
        argv.extend(["--type", type_])
    argv.append(value)
    if dry_run:
        return argv
    _log.info("kwriteconfig_nested", argv=argv)
    subprocess.run(argv, check=True)
    return argv


def kreadconfig(
    *,
    file: Path,
    group: str,
    key: str,
    type_: str = "string",
    dry_run: bool = False,
) -> str | None:
    """Call ``kreadconfig6`` to read a key; return ``None`` if unset/empty.

    Used to introspect the existing desktop containment ids so we write
    the wallpaper into the *real* containment group rather than the flat
    ``[Containments]`` group that Plasma ignores.
    """
    argv: list[str] = [
        ensure_tool("kreadconfig6"),
        "--file",
        str(file),
        "--group",
        group,
        "--key",
        key,
        "--type",
        type_,
    ]
    if dry_run:
        # Caller only wants the argv; return an empty placeholder.
        return ""
    _log.info("kreadconfig", argv=argv)
    proc = subprocess.run(argv, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _run_as_invoking_user(argv: list[str]) -> list[str]:
    """If running as root via sudo, drop to the invoking user.

    Live D-Bus calls must target the *original* user's session bus,
    otherwise Plasma services on ``/run/user/<uid>`` are not visible.
    We preserve ``XDG_RUNTIME_DIR`` and ``DBUS_SESSION_BUS_ADDRESS`` so
    the call reaches the user's PlasmaShell / ScreenSaver services.
    """
    sudo_user = os.environ.get("SUDO_USER")
    sudo_uid = os.environ.get("SUDO_UID")
    if os.geteuid() == 0 and sudo_user and sudo_uid:
        runtime = f"/run/user/{sudo_uid}"
        return [
            "sudo",
            "-u",
            sudo_user,
            "env",
            f"XDG_RUNTIME_DIR={runtime}",
            f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus",
            *argv,
        ]
    return argv


def qdbus_call(
    *,
    service: str,
    path: str,
    method: str,
    args: Sequence[str] = (),
    dry_run: bool = False,
) -> list[str]:
    """Call ``qdbus6`` to invoke a method.

    Returns the argv for dry-run inspection.

    Plasma is not always running (e.g. on a headless TTY or right after
    login). We treat a missing service as a soft success: the config
    files are updated, and Plasma will pick the wallpaper up on next
    start. We log at debug level so the user doesn't see noise.
    """
    argv: list[str] = _run_as_invoking_user(
        [ensure_tool("qdbus6"), service, path, method, *args]
    )
    if dry_run:
        return argv
    _log.info("qdbus_call", argv=argv)
    proc = subprocess.run(argv, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "does not exist" in stderr or "not found" in stderr.lower():
            _log.debug(
                "plasma_service_unavailable",
                service=service,
                hint="Plasma is not running; wallpaper will refresh on next start.",
            )
        else:
            _log.debug(
                "qdbus_call_failed",
                service=service,
                path=path,
                method=method,
                returncode=proc.returncode,
                stderr=stderr,
            )
    return argv


# Plasma 6 desktop-shell D-Bus service/object. The legacy
# ``org.kde.plasma.desktop`` service name does not exist on Plasma 6; the
# real shell service is ``org.kde.plasmashell``. There is no
# ``refreshWallpaper`` method — the canonical way to apply a wallpaper
# *live* (without a full config reload / visible flip) is to call
# ``evaluateScript`` with a small JS snippet that iterates every desktop
# containment and calls ``writeConfig`` on the wallpaper subgroup. This
# writes the correct ``[Containments][<id>][Wallpaper][org.kde.image]
# [General] Image=`` key AND applies it to the running shell atomically.
_PLASMASHELL_SERVICE = "org.kde.plasmashell"
_PLASMASHELL_PATH = "/PlasmaShell"
_PLASMASHELL_IFACE = "org.kde.PlasmaShell"


def evaluate_wallpaper_script(
    *,
    image_uri: str,
    plugin: str = "org.kde.image",
    dry_run: bool = False,
) -> list[str]:
    """Apply ``image_uri`` to every desktop containment *live* via the
    PlasmaShell ``evaluateScript`` D-Bus method.

    The script iterates all desktops(), sets the current config group to
    ``[Wallpaper][org.kde.image][General]`` and writes ``Image``. This is
    the same path Plasma's own wallpaper settings UI uses, so the change
    is applied to the running shell without a visible reload/flip.

    Returns the argv for dry-run inspection. Best-effort: if Plasma is not
    running (headless TTY, fresh boot) the call fails softly — the config
    file has already been written by ``kwriteconfig6`` and Plasma will
    pick it up on next start.
    """
    # Escape backslashes and single quotes for the JS string literal.
    js_image = image_uri.replace("\\", "\\\\").replace("'", "\\'")
    js_plugin = plugin.replace("\\", "\\\\").replace("'", "\\'")
    script = (
        "var all = desktops();"
        "for (var i=0;i<all.length;i++){"
        "var d=all[i];"
        f"d.currentConfigGroup=['Wallpaper','{js_plugin}','General'];"
        f"d.writeConfig('Image','{js_image}');"
        "}"
    )
    argv: list[str] = _run_as_invoking_user(
        [
            ensure_tool("qdbus6"),
            _PLASMASHELL_SERVICE,
            _PLASMASHELL_PATH,
            f"{_PLASMASHELL_IFACE}.evaluateScript",
            script,
        ]
    )
    if dry_run:
        return argv
    _log.info("evaluate_wallpaper_script", argv=argv)
    proc = subprocess.run(argv, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "does not exist" in stderr or "not found" in stderr.lower():
            _log.debug(
                "plasmashell_unavailable",
                hint="Plasma is not running; desktop wallpaper will refresh on next start.",
            )
        else:
            _log.warning(
                "evaluate_wallpaper_script_failed",
                returncode=proc.returncode,
                stderr=stderr,
            )
    return argv


def reload_lockscreen_config(*, dry_run: bool = False) -> list[str]:
    """Ask the running kscreenlocker to re-read its config.

    Plasma 6 exposes ``org.kde.screensaver.configure()`` on the
    ``org.freedesktop.ScreenSaver`` object at
    ``/org/freedesktop/ScreenSaver``. This reloads the lock-screen
    wallpaper plugin so the *next* lock uses the new image, without
    needing to lock+unlock. Best-effort: a no-op if the service is absent.
    """
    argv: list[str] = _run_as_invoking_user(
        [
            ensure_tool("qdbus6"),
            "org.freedesktop.ScreenSaver",
            "/org/freedesktop/ScreenSaver",
            "org.kde.screensaver.configure",
        ]
    )
    if dry_run:
        return argv
    _log.info("reload_lockscreen_config", argv=argv)
    proc = subprocess.run(argv, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "does not exist" in stderr or "not found" in stderr.lower():
            _log.debug(
                "screensaver_service_unavailable",
                hint="ScreenSaver service not running; lock screen will reload on next lock.",
            )
        else:
            _log.warning(
                "reload_lockscreen_config_failed",
                returncode=proc.returncode,
                stderr=stderr,
            )
    return argv
