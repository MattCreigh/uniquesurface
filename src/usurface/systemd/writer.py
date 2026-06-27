"""Render systemd user units from Jinja templates."""

from __future__ import annotations

import os
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader


from usurface import paths

def _get_unit_dir() -> Path:
    return paths.config_dir() / "systemd" / "user"


def _template_env() -> Environment:
    template_dir = files("usurface.systemd").joinpath("templates")  # type: ignore[arg-type]
    # FileSystemLoader needs a real string path.
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env


def render_service(context: dict[str, Any]) -> str:
    env = _template_env()
    template = env.get_template("usurface-pull.service.j2")
    return template.render(**context)


def render_timer() -> str:
    env = _template_env()
    template = env.get_template("usurface-pull.timer.j2")
    return template.render()


def install(
    *,
    unit_dir: Path | None = None,
    usurface_bin: str | None = None,
    working_dir: str | None = None,
) -> tuple[Path, Path]:
    """Write ``.service`` and ``.timer`` into ``unit_dir`` (default user dir).

    Returns ``(service_path, timer_path)``. Does not run ``systemctl``.
    """
    target_dir = (unit_dir or _get_unit_dir())
    target_dir.mkdir(parents=True, exist_ok=True)

    bin_path = usurface_bin or shutil.which("usurface") or "/usr/local/bin/usurface"
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


def is_enabled() -> bool:
    res = systemctl("is-enabled", "usurface-pull.timer")
    return res.stdout.strip() in ("enabled", "static")
