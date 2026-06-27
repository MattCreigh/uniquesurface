"""Login-screen wallpaper backend.

Patches the SDDM Breeze theme's ``theme.conf`` to point at the chosen
wallpaper. QML font / theme-token patching lives in
:mod:`usurface.theme.qml_patch` and is invoked by the orchestrator,
not here.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from usurface.manifest import Manifest

_BG_LINE_RE = re.compile(r"^(\s*background\s*=\s*).*$", re.MULTILINE)
_THEME_CONF_PATH = Path("/usr/share/sddm/themes/breeze/theme.conf")
_DEFAULT_COMMENT = "# managed by usurface"


class LoginBackend:
    """Writes the SDDM theme.conf ``background=`` line.

    If ``theme.conf`` does not exist (e.g. an unsupported SDDM theme
    is installed) the backend logs and does nothing.
    """

    name = "login"

    def apply(self, manifest: Manifest, wallpaper: Path) -> None:
        if not _THEME_CONF_PATH.exists():
            return
        self._write_conf(wallpaper)

    def dry_run_plan(self, wallpaper: Path) -> list[str]:
        if not _THEME_CONF_PATH.exists():
            return [f"# {self.name}: {_THEME_CONF_PATH} not present; skipped"]
        return [
            f"edit {_THEME_CONF_PATH}: set background={wallpaper}",
        ]

    def _write_conf(self, wallpaper: Path) -> None:
        target = wallpaper.resolve()
        if not os.access(_THEME_CONF_PATH, os.W_OK):
            if os.geteuid() != 0:
                raise PermissionError(
                    f"{_THEME_CONF_PATH} is not writable and we are not root"
                )
        text = _THEME_CONF_PATH.read_text(encoding="utf-8")
        if _BG_LINE_RE.search(text):
            new_text = _BG_LINE_RE.sub(rf"\g<1>{target}", text)
        else:
            sep = "" if text.endswith("\n") else "\n"
            new_text = f"{text}{sep}{_DEFAULT_COMMENT}\nbackground={target}\n"
        tmp = _THEME_CONF_PATH.with_suffix(".conf.usurface.tmp")
        from usurface.atomic import atomic_write_bytes

        atomic_write_bytes(tmp, new_text.encode("utf-8"), mode=0o644)
        os.replace(tmp, _THEME_CONF_PATH)
