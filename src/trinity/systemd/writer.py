"""Render systemd user units from embedded templates."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from trinity import paths

# Default timeout for ``systemctl --user`` calls. Long enough for a slow
# first D-Bus call, short enough that a hung user manager surfaces as an
# error instead of freezing the CLI.
_SYSTEMCTL_TIMEOUT = 10.0

_SERVICE_TEMPLATE = """\
[Unit]
Description=trinity — refresh POTD wallpaper when the source changed
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# --if-changed: probe the provider with a metadata-sized request and
# only download + apply when the image actually changed, so the hourly
# timer is cheap for both us and the provider.
ExecStart={trinity_bin} apply --if-changed
WorkingDirectory={home_dir}
StandardOutput=journal
StandardError=journal
# --- Hardening (enterprise Linux best practice) ---
# Kill the service if it runs longer than 2 minutes (a hung HTTP download
# or a frozen kwriteconfig6 should not block the timer indefinitely).
TimeoutStartSec=120
# Kill the service if it takes longer than 30s to stop cleanly (e.g. a
# hung D-Bus call during systemctl stop). systemd's default is 90s;
# making it explicit documents the intent and surfaces a misbehaving
# D-Bus call sooner in the unit logs.
TimeoutStopSec=30
# Sandbox: restrict what the service can do even if compromised.
# trinity needs: write to ~/.config/trinity + ~/.local/state/trinity +
# /usr/local/share/wallpapers; network for the Bing provider; no new
# privileges; no access to /tmp (PrivateTmp); read-only access to /home
# except the state/config dirs above.
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%h/.config/trinity %h/.local/state/trinity /usr/local/share/wallpapers
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources
SystemCallArchitectures=native
RestrictRealtime=true
RestrictNamespaces=true
RestrictSUIDSGID=true
LockPersonality=true
ProtectKernelTunables=true
ProtectControlGroups=true
ProtectHostname=true
UMask=0022
# NOTE: ProtectClock= and ProtectKernelModules= are deliberately absent.
# They are implemented by dropping capabilities, which a *user* manager
# cannot do — on Ubuntu 24.04-based systems (KDE Neon) the unit fails to
# start with "Failed to drop capabilities: Operation not permitted"
# before ExecStart even runs. They add nothing for a user service anyway:
# an unprivileged process never holds CAP_SYS_TIME / CAP_SYS_MODULE.
"""

# Hourly conditional schedule. POTD sources publish at provider-specific
# times (Bing rotates the en-US image in the early morning UTC, feeds
# publish whenever), so the previous fixed daily run lagged upstream by
# hours. Each run is ``apply --if-changed``: a metadata-sized probe that
# skips the download + surface writes when nothing changed, so hourly
# polling costs a few KiB/hour. ``RandomizedDelaySec`` spreads a fleet of
# machines so they don't all hit the provider simultaneously;
# ``Persistent=true`` catches up a run missed while asleep/offline on the
# next boot, so a new image lands promptly after wake.
_TIMER_TEMPLATE = """\
[Unit]
Description=trinity — hourly POTD refresh (skips when unchanged)

[Timer]
OnCalendar=hourly
RandomizedDelaySec=10min
Persistent=true
Unit=trinity-pull.service

[Install]
WantedBy=timers.target
"""


# Optional wake-enabled timer template.  Used when ``trinity install
# --wake-network`` is passed.  ``WakeSystem=true`` tells systemd to
# configure an RTC wakealarm so the laptop wakes from s2idle/S0ix
# around the next trigger.  This is hardware-dependent and opt-in.
_WAKE_TIMER_TEMPLATE = """\
[Unit]
Description=trinity — hourly POTD refresh (skips when unchanged, wakes system)

[Timer]
OnCalendar=hourly
RandomizedDelaySec=10min
Persistent=true
WakeSystem=true
Unit=trinity-pull.service

[Install]
WantedBy=timers.target
"""


class TrinityBinaryNotFound(RuntimeError):
    """Raised when the ``trinity`` console script cannot be located.

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


def render_wake_timer() -> str:
    """Render the wake-enabled timer template (WakeSystem=true)."""
    return _WAKE_TIMER_TEMPLATE


def install(
    *,
    unit_dir: Path | None = None,
    trinity_bin: str | None = None,
    working_dir: str | None = None,
    wake_system: bool = False,
) -> tuple[Path, Path]:
    """Write ``.service`` and ``.timer`` into ``unit_dir`` (default user dir).

    Returns ``(service_path, timer_path)``. Does not run ``systemctl``.
    When ``wake_system`` is True, uses the wake-enabled timer template
    (``WakeSystem=true``).
    """
    target_dir = unit_dir or _get_unit_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    if trinity_bin is not None:
        bin_path = trinity_bin
    else:
        found = shutil.which("trinity")
        if not found:
            raise TrinityBinaryNotFound(
                "could not locate the 'trinity' console script on PATH; "
                "install the package (e.g. `uv tool install .` or "
                "`pip install --user .`) and re-run `trinity install`, "
                "or pass an explicit --trinity-bin"
            )
        bin_path = found
    # WorkingDirectory defaults to the invoking user's home, not the
    # install-time CWD (which could be /root under sudo). The service
    # only needs a valid CWD for relative-path resolution; the home dir
    # is the safest default and matches what systemd user services
    # inherit anyway.
    home_dir = working_dir or str(paths.invoking_user_home() or Path.home())

    svc_text = render_service({"trinity_bin": bin_path, "home_dir": home_dir})
    tmr_text = render_wake_timer() if wake_system else render_timer()

    svc = target_dir / "trinity-pull.service"
    tmr = target_dir / "trinity-pull.timer"

    from trinity.atomic import atomic_write_text

    atomic_write_text(svc, svc_text, mode=0o644)
    atomic_write_text(tmr, tmr_text, mode=0o644)
    return svc, tmr


def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``systemctl --user`` with the given arguments.

    Caller checks return code / stderr as needed. If ``systemctl`` (or
    ``sudo`` when dropping privileges) is not on PATH — e.g. a container
    or a non-systemd distribution — a synthetic failed result is
    returned instead of raising, so ``status``/``doctor`` keep working.
    """
    systemctl_bin = shutil.which("systemctl")
    if systemctl_bin is None:
        return subprocess.CompletedProcess(
            args=("systemctl", "--user", *args),
            returncode=127,
            stdout="",
            stderr="systemctl not found on PATH",
        )
    cmd = [systemctl_bin, "--user", *args]
    sudo_user = os.environ.get("SUDO_USER")
    sudo_uid = os.environ.get("SUDO_UID")
    if sudo_user and sudo_uid and os.geteuid() == 0:
        sudo_bin = shutil.which("sudo")
        if sudo_bin is None:
            return subprocess.CompletedProcess(
                args=tuple(cmd),
                returncode=127,
                stdout="",
                stderr="sudo not found on PATH; cannot drop to the invoking user",
            )
        runtime = f"/run/user/{sudo_uid}"
        cmd = [
            sudo_bin,
            "-u",
            sudo_user,
            "env",
            f"XDG_RUNTIME_DIR={runtime}",
            f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus",
            *cmd,
        ]
    return subprocess.run(
        cmd, check=False, capture_output=True, text=True, timeout=_SYSTEMCTL_TIMEOUT
    )


def enable_and_start() -> tuple[bool, str]:
    """Reload, enable, and start the timer. Returns ``(success, message)``."""
    systemctl("daemon-reload")
    res = systemctl("enable", "--now", "trinity-pull.timer")
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip()
    return True, "enabled and started trinity-pull.timer"


def disable_and_stop() -> tuple[bool, str]:
    res = systemctl("disable", "--now", "trinity-pull.timer")
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip()
    return True, "disabled trinity-pull.timer"


def pause() -> tuple[bool, str]:
    """Mask the timer so it will not trigger until resumed."""
    # Use --runtime for user units: a persistent mask symlink cannot be
    # created in the same directory that already contains the unit file,
    # which causes 'Failed to mask unit: File ... already exists'.
    res = systemctl("mask", "--runtime", "trinity-pull.timer")
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip()
    return True, "paused trinity-pull.timer (runtime masked)"


def resume() -> tuple[bool, str]:
    """Unmask and re-enable the timer."""
    res = systemctl("unmask", "--runtime", "trinity-pull.timer")
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip()
    res = systemctl("enable", "trinity-pull.timer")
    if res.returncode != 0:
        return False, res.stderr.strip() or res.stdout.strip()
    return True, "resumed trinity-pull.timer"


def is_paused() -> bool:
    """Return True if the timer is currently masked.

    systemd reports a runtime mask as ``enabled`` from ``is-enabled``
    because the persistent unit file is unchanged. Runtime masks are
    represented by a symlink under ``$XDG_RUNTIME_DIR/systemd/user/``
    pointing to ``/dev/null``, so we check that path as well.
    """
    res = systemctl("is-enabled", "trinity-pull.timer")
    if res.stdout.strip() == "masked":
        return True

    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    runtime_link = runtime_dir / "systemd" / "user" / "trinity-pull.timer"
    try:
        if runtime_link.is_symlink() and os.readlink(runtime_link) == "/dev/null":
            return True
    except OSError:
        # Symlink may have been removed between is_symlink() and readlink().
        pass
    return False


def is_enabled() -> bool:
    res = systemctl("is-enabled", "trinity-pull.timer")
    return res.stdout.strip() in ("enabled", "static")
