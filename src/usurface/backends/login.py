"""Login-screen wallpaper backend.

Patches the SDDM Breeze theme's ``theme.conf`` to point at the chosen
wallpaper and (optionally) set the accent/solid ``color=`` key. QML
font / theme-token patching lives in :mod:`usurface.theme.qml_patch`
and is invoked by the orchestrator, not here.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from usurface.backends.base import BackendError
from usurface.manifest import Manifest

_BG_LINE_RE = re.compile(r"^(\s*background\s*=\s*).*$", re.MULTILINE)
_COLOR_LINE_RE = re.compile(r"^(\s*color\s*=\s*).*$", re.MULTILINE)
_THEME_CONF_PATH = Path("/usr/share/sddm/themes/breeze/theme.conf")
_DEFAULT_COMMENT = "# managed by usurface"


class LoginBackend:
    """Writes the SDDM theme.conf ``background=`` line.

    If ``accent_color`` is provided, also writes ``color=`` (the SDDM
    Breeze theme's solid-background colour, read in Main.qml as
    ``config.color`` → ``sceneBackgroundColor``).

    If ``theme.conf`` does not exist (e.g. an unsupported SDDM theme
    is installed) the backend logs and does nothing.
    """

    name = "login"

    def __init__(self, *, accent_color: str | None = None) -> None:
        self._accent_color = accent_color

    def apply(self, manifest: Manifest, wallpaper: Path) -> None:
        if not _THEME_CONF_PATH.exists():
            return
        self._write_conf(manifest, wallpaper)

    def dry_run_plan(self, wallpaper: Path) -> list[str]:
        if not _THEME_CONF_PATH.exists():
            return [f"# {self.name}: {_THEME_CONF_PATH} not present; skipped"]
        plan = [f"edit {_THEME_CONF_PATH}: set background={wallpaper}"]
        if self._accent_color is not None:
            plan.append(f"edit {_THEME_CONF_PATH}: set color={self._accent_color}")
        return plan

    def _write_conf(self, manifest: Manifest, wallpaper: Path) -> None:
        target = wallpaper.resolve()
        if not _can_write(_THEME_CONF_PATH):
            raise BackendError(
                f"{_THEME_CONF_PATH} is not writable",
                hint=(
                    "the SDDM theme file requires root. Re-run with sudo, e.g.\n"
                    "  sudo usurface apply"
                ),
            )
        text = _THEME_CONF_PATH.read_text(encoding="utf-8")
        new_text = _set_key(text, _BG_LINE_RE, "background", str(target))
        if self._accent_color is not None:
            new_text = _set_key(
                new_text, _COLOR_LINE_RE, "color", self._accent_color
            )

        from usurface.manifest import write_tracked

        write_tracked(
            manifest, _THEME_CONF_PATH, new_text.encode("utf-8"), mode=0o644
        )


def _set_key(text: str, line_re: re.Pattern[str], key: str, value: str) -> str:
    """Set ``key=value`` in a theme.conf-style INI text.

    If the key already exists, replace its value via ``line_re``; otherwise
    append ``key=value`` (with the managed-by-usurface comment) on a new
    line. Preserves all other lines.
    """
    if line_re.search(text):
        return line_re.sub(rf"\g<1>{value}", text)
    sep = "" if text.endswith("\n") else "\n"
    return f"{text}{sep}{_DEFAULT_COMMENT}\n{key}={value}\n"


def _can_write(path: Path) -> bool:
    """Return True if the current process can write to ``path``.

    Checks the parent directory as well, because the file may be
    replaced atomically (which requires write access on the directory).
    """
    if path.exists():
        return os.access(path, os.W_OK)
    return os.access(path.parent, os.W_OK)


def login_surface_needs_root() -> bool:
    """Return True if the SDDM login surface is present but not writable
    by the current user (i.e. the apply step for login will require root).

    Encapsulates the path-existence + writability + euid check so the
    CLI doesn't need to import the private ``_THEME_CONF_PATH`` or
    reimplement ``_can_write``.
    """
    if not _THEME_CONF_PATH.exists():
        return False
    if os.geteuid() == 0:
        return False
    return not _can_write(_THEME_CONF_PATH)
