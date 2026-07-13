"""Login screen (SDDM/plasmalogin) background backend."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from trinity.backends.base import Backend, BackendError
from trinity.manifest import Manifest

_THEME_CONF_PATH = Path("/usr/share/sddm/themes/breeze/theme.conf")
_THEME_CONF_USER_PATH = Path("/usr/share/sddm/themes/breeze/theme.conf.user")

_PLASMALOGIN_CONF_DIR = Path("/etc/plasmalogin.conf.d")
_PLASMALOGIN_DROPIN = _PLASMALOGIN_CONF_DIR / "trinity.conf"

_DEFAULT_COMMENT = "# Written by Trinity — unified surface manager."


def is_plasmalogin_active() -> bool:
    """Return True if plasmalogin is the currently active DM unit.

    Neon systems run plasmalogin (which overrides SDDM configuration).
    In this case, modifying SDDM Breeze configs is ignored, and we must
    write the greeter wallpaper configuration to /etc/plasmalogin.conf.d/
    instead.
    """
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False
    # is-active checks if the unit is loaded and running. --quiet suppresses
    # output; exit code 0 means active.
    res = subprocess.run(
        [systemctl, "is-active", "--quiet", "plasmalogin"],
        check=False,
        capture_output=True,
    )
    return res.returncode == 0


class LoginBackend(Backend):
    """Writes the SDDM theme.conf ``background=`` line.

    If ``accent_color`` is provided, also writes ``color=`` (the SDDM
    Breeze theme's solid-background colour, read in Main.qml as
    ``config.color`` → ``sceneBackgroundColor``).

    If ``theme.conf`` does not exist (e.g. an unsupported SDDM theme
    is installed) the backend logs and does nothing.

    For plasmalogin, writes to /etc/plasmalogin.conf.d/trinity.conf.
    """

    name = "login"

    def __init__(
        self, *, accent_color: str | None = None, forked: bool = False
    ) -> None:
        self._accent_color = accent_color
        self._forked = forked

    @property
    def forked(self) -> bool:
        from trinity.backends.sddm_fork import FORK_THEME_DIR

        return self._forked or FORK_THEME_DIR.is_dir()

    def apply(self, manifest: Manifest, wallpaper: Path) -> None:
        if is_plasmalogin_active():
            self._write_plasmalogin_conf(manifest, wallpaper)
            return
        if not _THEME_CONF_PATH.exists() and not self.forked:
            return
        self._write_conf(manifest, wallpaper)

    def dry_run_plan(self, wallpaper: Path) -> list[str]:
        if is_plasmalogin_active():
            return [
                f"write {_PLASMALOGIN_DROPIN}: set WallpaperPluginId=org.kde.image",
                f"write {_PLASMALOGIN_DROPIN}: set Image=file://{wallpaper}",
            ]
        from trinity.backends.sddm_fork import FORK_THEME_DIR

        user_path = (
            FORK_THEME_DIR / "theme.conf.user" if self.forked else _THEME_CONF_USER_PATH
        )
        if not _THEME_CONF_PATH.exists() and not self.forked:
            return [f"# {self.name}: {user_path.parent} not present; skipped"]
        plan = [f"write {user_path}: set background={wallpaper}"]
        if self._accent_color is not None:
            plan.append(f"write {user_path}: set color={self._accent_color}")
        return plan

    def _write_conf(self, manifest: Manifest, wallpaper: Path) -> None:
        # Absolute path, but WITHOUT resolving symlinks: the orchestrator
        # passes the stable `last_wallpaper.jpg` alias on purpose — SDDM
        # re-reads the file at greeter start, and the alias tracks the
        # current image without theme.conf.user (usually root-owned)
        # needing a rewrite. Resolving would pin the hash-named target.
        target = wallpaper.absolute()
        from trinity.backends.sddm_fork import FORK_THEME_DIR

        user_path = (
            FORK_THEME_DIR / "theme.conf.user" if self.forked else _THEME_CONF_USER_PATH
        )
        if not _can_write(user_path):
            raise BackendError(
                f"{user_path.parent} is not writable",
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
            user_path,
            new_text.encode("utf-8"),
            mode=0o644,
        )

    def _write_plasmalogin_conf(self, manifest: Manifest, wallpaper: Path) -> None:
        target = wallpaper.absolute()
        if not _can_write(_PLASMALOGIN_DROPIN):
            raise BackendError(
                f"{_PLASMALOGIN_CONF_DIR} is not writable",
                hint=(
                    "the plasmalogin configuration directory requires root. "
                    "Re-run with sudo, e.g.\n  sudo trinity apply"
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


def login_surface_needs_root(theme_tokens_enabled: bool = False) -> bool:
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

    if os.geteuid() == 0:
        return False

    if theme_tokens_enabled:
        from trinity.backends.sddm_fork import DROPIN_PATH, FORK_THEME_DIR

        if not _can_write(DROPIN_PATH):
            return True
        if not FORK_THEME_DIR.is_dir():
            if not _can_write(FORK_THEME_DIR.parent):
                return True
        else:
            if not _can_write(FORK_THEME_DIR / "theme.conf.user"):
                return True
        return False

    # Wallpaper-only mode (traditional SDDM theme.conf.user)
    if not _THEME_CONF_PATH.exists():
        return False
    return not _can_write(_THEME_CONF_USER_PATH)
