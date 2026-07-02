"""Render systemd user units from embedded templates."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from usurface import paths

_SERVICE_TEMPLATE = """\
[Unit]
Description=uniquesurface — refresh daily POTD wallpaper
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={usurface_bin} apply
WorkingDirectory={working_dir}
StandardOutput=journal
StandardError=journal
"""

# Deterministic daily schedule: noon local time, with a small jitter so a
# fleet of machines doesn't all hit Bing simultaneously. ``Persistent=true``
# ensures a missed run (e.g. laptop was asleep/offline) catches up on next
# boot. Noon was chosen to match the historical apply time and avoid midnight
# network quirks; a missed noon run is caught up the following boot.
_TIMER_TEMPLATE = """\
[Unit]
Description=uniquesurface — daily POTD refresh

[Timer]
OnCalendar=*-*-* 12:00:00
RandomizedDelaySec=15min
Persistent=true
Unit=usurface-pull.service

[Install]
WantedBy=timers.target
"""


class UsurfaceBinaryNotFound(RuntimeError):
    """Raised when the ``usurface`` console script cannot be located.

    A scheduled systemd service that points at a non-existent binary
    fails with status 203/EXEC and silently stops refreshing the
    wallpaper. We refuse to write such a unit rather than guessing a
    path that may not exist on the target machine.
    """


def _get_unit_dir() -> Path:
    return paths.config_dir().parent / "systemd" / "user"


def render_service(context: dict[str, Any]) -> str:
    return _SERVICE_TEMPLATE.format(**context)


def render_timer() -> str:
    return _TIMER_TEMPLATE


def install(
    *,
    unit_dir: Path | None = None,
    usurface_bin: str | None = None,
    working_dir: str | None = None,
) -> tuple[Path, Path]:
    """Write ``.service`` and ``.timer`` into ``unit_dir`` (default user dir).

    Returns ``(service_path, timer_path)``. Does not run ``systemctl``.
    """
    target_dir = unit_dir or _get_unit_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    if usurface_bin is not None:
        bin_path = usurface_bin
    else:
        found = shutil.which("usurface")
        if not found:
            raise UsurfaceBinaryNotFound(
                "could not locate the 'usurface' console script on PATH; "
                "install the package (e.g. `uv tool install .` or "
                "`pip install --user .`) and re-run `usurface install`, "
                "or pass an explicit --usurface-bin"
            )
        bin_path = found
    cwd = working_dir or os.getcwd()

    svc_text = render_service({"usurface_bin": bin_path, "working_dir": cwd})
    tmr_text = render_timer()

    svc = target_dir / "usurface-pull.service"
    tmr = target_dir / "usurface-pull.timer"

    from usurface.atomic import atomic_write_text

    atomic_write_text(svc, svc_text, mode=0o644)
    atomic_write_text(tmr, tmr_text, mode=0o644)
    return svc, tmr


def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``systemctl --user`` with the given arguments.

    Caller checks return code / stderr as needed.
    """
    cmd = ["systemctl", "--user", *args]
    sudo_user = os.environ.get("SUDO_USER")
    sudo_uid = os.environ.get("SUDO_UID")
    if sudo_user and sudo_uid and os.geteuid() == 0:
        cmd = [
            "sudo",
            "-u",
            sudo_user,
            "env",
            f"XDG_RUNTIME_DIR=/run/user/{sudo_uid}",
            *cmd,
        ]
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def enable_and_start() -> tuple[bool, str]:
    """Reload, enable, and start the timer. Returns ``(success, message)``."""
    systemctl("daemon-reload")
    res = systemctl("enable", "--now", "usurface-pull.timer")
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip()
    return True, "enabled and started usurface-pull.timer"


def disable_and_stop() -> tuple[bool, str]:
    res = systemctl("disable", "--now", "usurface-pull.timer")
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip()
    return True, "disabled usurface-pull.timer"


def pause() -> tuple[bool, str]:
    """Mask the timer so it will not trigger until resumed."""
    # Use --runtime for user units: a persistent mask symlink cannot be
    # created in the same directory that already contains the unit file,
    # which causes 'Failed to mask unit: File ... already exists'.
    res = systemctl("mask", "--runtime", "usurface-pull.timer")
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip()
    return True, "paused usurface-pull.timer (runtime masked)"


def resume() -> tuple[bool, str]:
    """Unmask and re-enable the timer."""
    res = systemctl("unmask", "--runtime", "usurface-pull.timer")
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip()
    systemctl("enable", "usurface-pull.timer")
    return True, "resumed usurface-pull.timer"


def is_paused() -> bool:
    """Return True if the timer is currently masked.

    systemd reports a runtime mask as ``enabled`` from ``is-enabled``
    because the persistent unit file is unchanged. Runtime masks are
    represented by a symlink under ``$XDG_RUNTIME_DIR/systemd/user/``
    pointing to ``/dev/null``, so we check that path as well.
    """
    res = systemctl("is-enabled", "usurface-pull.timer")
    if res.stdout.strip() == "masked":
        return True

    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    runtime_link = runtime_dir / "systemd" / "user" / "usurface-pull.timer"
    if runtime_link.is_symlink() and os.readlink(runtime_link) == "/dev/null":
        return True
    return False


def is_enabled() -> bool:
    res = systemctl("is-enabled", "usurface-pull.timer")
    return res.stdout.strip() in ("enabled", "static")
