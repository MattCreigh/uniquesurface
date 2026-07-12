"""Login-screen wallpaper backend.

Patches the SDDM Breeze theme's ``theme.conf`` to point at the chosen
wallpaper and (optionally) set the accent/solid ``color=`` key. QML
font / theme-token patching lives in :mod:`trinity.theme.qml_patch`
and is invoked by the orchestrator, not here.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from trinity.backends.base import BackendError
from trinity.manifest import Manifest

_BG_LINE_RE = re.compile(r"^(\s*background\s*=\s*).*$", re.MULTILINE)
_COLOR_LINE_RE = re.compile(r"^(\s*color\s*=\s*).*$", re.MULTILINE)
_THEME_CONF_PATH = Path("/usr/share/sddm/themes/breeze/theme.conf")
# As of Phase 5 we also write ``theme.conf.user`` alongside the base
# config.  SDDM merges ``theme.conf.user`` over ``theme.conf`` so this
# avoids editing the vendor file — the wallpaper change is reversible
# by deleting ``theme.conf.user``.
_THEME_CONF_USER_PATH = Path("/usr/share/sddm/themes/breeze/theme.conf.user")
_DEFAULT_COMMENT = "# managed by trinity"
_PLASMALOGIN_CONF_DIR = Path("/etc/plasmalogin.conf.d")
_PLASMALOGIN_DROPIN = Path("/etc/plasmalogin.conf.d/trinity.conf")


def is_plasmalogin_active() -> bool:
    """Return True if plasmalogin is the active display manager."""
    import shutil
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return Path("/usr/lib/plasmalogin").is_dir()
    import subprocess
    try:
        probe = subprocess.run(
            [systemctl, "is-active", "--quiet", "plasmalogin"],
            check=False,
            capture_output=True,
        )
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class LoginBackend:
    """Writes the SDDM theme.conf ``background=`` line.

    If ``accent_color`` is provided, also writes ``color=`` (the SDDM
    Breeze theme's solid-background colour, read in Main.qml as
    ``config.color`` → ``sceneBackgroundColor``).

    If ``theme.conf`` does not exist (e.g. an unsupported SDDM theme
    is installed) the backend logs and does nothing.

    For plasmalogin, writes to /etc/plasmalogin.conf.d/trinity.conf.
    """

    name = "login"

    def __init__(self, *, accent_color: str | None = None) -> None:
        self._accent_color = accent_color


    def apply(self, manifest: Manifest, wallpaper: Path) -> None:
        if is_plasmalogin_active():
            self._write_plasmalogin_conf(manifest, wallpaper)
            return
        if not _THEME_CONF_PATH.exists():
            return
        self._write_conf(manifest, wallpaper)

    def dry_run_plan(self, wallpaper: Path) -> list[str]:
        if is_plasmalogin_active():
            return [
                f"write {_PLASMALOGIN_DROPIN}: set WallpaperPluginId=org.kde.image",
                f"write {_PLASMALOGIN_DROPIN}: set Image=file://{wallpaper}",
            ]
        if not _THEME_CONF_PATH.exists():
            return [f"# {self.name}: {_THEME_CONF_PATH} not present; skipped"]
        # Phase 5: wallpaper-only path writes theme.conf.user (no
        # vendor file edit).  See docs/design/override-mechanisms.md.
        plan = [f"write {_THEME_CONF_USER_PATH}: set background={wallpaper}"]
        if self._accent_color is not None:
            plan.append(
                f"write {_THEME_CONF_USER_PATH}: set color={self._accent_color}"
            )
        return plan

    def _write_conf(self, manifest: Manifest, wallpaper: Path) -> None:
        # Absolute path, but WITHOUT resolving symlinks: the orchestrator
        # passes the stable `last_wallpaper.jpg` alias on purpose — SDDM
        # re-reads the file at greeter start, and the alias tracks the
        # current image without theme.conf.user (usually root-owned)
        # needing a rewrite. Resolving would pin the hash-named target.
        target = wallpaper.absolute()
        # Phase 5: write theme.conf.user (the sanctioned SDDM override
        # mechanism) rather than editing the vendor theme.conf.  SDDM
        # merges .user over the base config, so this is reversible by
        # deleting theme.conf.user.
        if not _can_write(_THEME_CONF_USER_PATH):
            raise BackendError(
                f"{_THEME_CONF_USER_PATH.parent} is not writable",
                hint=(
                    "the SDDM theme directory requires root. Re-run with "
                    "sudo, e.g.\n  sudo trinity apply"
                ),
            )
        # theme.conf.user may not exist yet; build a fresh minimal
        # config rather than patching the existing one, so a stale
        # value from a previous run is replaced cleanly.
        lines = [_DEFAULT_COMMENT, f"background={target}"]
        if self._accent_color is not None:
            lines.append(f"color={self._accent_color}")
        new_text = "\n".join(lines) + "\n"

        from trinity.manifest import write_tracked

        write_tracked(
            manifest,
            _THEME_CONF_USER_PATH,
            new_text.encode("utf-8"),
            mode=0o644,
        )

    def _write_plasmalogin_conf(self, manifest: Manifest, wallpaper: Path) -> None:
        target = wallpaper.absolute()
        if not _can_write(_PLASMALOGIN_DROPIN):
            raise BackendError(
                f"{_PLASMALOGIN_CONF_DIR} is not writable",
                hint=(
                    "the plasmalogin configuration directory requires root. Re-run with "
                    "sudo, e.g.\n  sudo trinity apply"
                ),
            )
        lines = [
            "[Greeter]",
            "WallpaperPluginId=org.kde.image",
            "",
            "[Greeter][Wallpaper][org.kde.image][General]",
            f"Image=file://{target}",
        ]
        new_text = "\n".join(lines) + "\n"

        from trinity.manifest import write_tracked

        _PLASMALOGIN_CONF_DIR.mkdir(parents=True, exist_ok=True)
        write_tracked(
            manifest,
            _PLASMALOGIN_DROPIN,
            new_text.encode("utf-8"),
            mode=0o644,
        )


def _set_key(text: str, line_re: re.Pattern[str], key: str, value: str) -> str:
    """Set ``key=value`` in a theme.conf-style INI text.

    If the key already exists, replace its value via ``line_re``; otherwise
    append ``key=value`` (with the managed-by-trinity comment) on a new
    line. Preserves all other lines.
    """
    if line_re.search(text):
        return line_re.sub(rf"\g<1>{value}", text)
    sep = "" if text.endswith("\n") else "\n"
    return f"{text}{sep}{_DEFAULT_COMMENT}\n{key}={value}\n"


def _can_write(path: Path) -> bool:
    """Return True if the current process can write to ``path``.

    Checks the parent directories as well, because the directory or file
    may be created or replaced atomically (which requires write access
    on the first existing ancestor directory).
    """
    if path.exists():
        return os.access(path, os.W_OK)
    p = path.parent
    while not p.exists() and p != p.parent:
        p = p.parent
    return os.access(p, os.W_OK)



def login_surface_needs_root() -> bool:
    """Return True if the SDDM or plasmalogin login surface is present but
    not writable by the current user (i.e. the apply step for login will
    require root).

    Encapsulates the path-existence + writability + euid check so the
    CLI doesn't need to import private paths or duplicate logic.
    """
    if is_plasmalogin_active():
        if os.geteuid() == 0:
            return False
        return not _can_write(_PLASMALOGIN_DROPIN)

    if not _THEME_CONF_PATH.exists():
        return False
    if os.geteuid() == 0:
        return False
    return not _can_write(_THEME_CONF_USER_PATH)

