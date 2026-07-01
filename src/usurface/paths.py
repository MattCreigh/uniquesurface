"""XDG-aware paths for usurface state, config, and cache.

All paths are absolute. ``shared_wallpapers_dir()`` returns the
plasmalogin-visible location and accepts ``$USURFACE_SHARED_DIR`` to
override at runtime.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_state_dir

_APP_NAME = "usurface"
_APP_AUTHOR = "uniquesurface"


def _get_user_home() -> Path | None:
    """Return the original user's home directory if running via sudo, else None."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and os.geteuid() == 0:
        import pwd

        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return None


def config_dir() -> Path:
    """Return ``~/.config/usurface`` (XDG_CONFIG_HOME aware)."""
    sudo_home = _get_user_home()
    if sudo_home:
        return sudo_home / ".config" / _APP_NAME
    return Path(user_config_dir(_APP_NAME, _APP_AUTHOR, roaming=False))


def state_dir() -> Path:
    """Return ``~/.local/state/usurface`` (XDG_STATE_HOME aware)."""
    sudo_home = _get_user_home()
    if sudo_home:
        return sudo_home / ".local" / "state" / _APP_NAME
    return Path(user_state_dir(_APP_NAME, _APP_AUTHOR, roaming=False))


def cache_dir() -> Path:
    """Return ``~/.cache/usurface`` (XDG_CACHE_HOME aware)."""
    sudo_home = _get_user_home()
    if sudo_home:
        return sudo_home / ".cache" / _APP_NAME
    return Path(user_cache_dir(_APP_NAME, _APP_AUTHOR))


def config_file() -> Path:
    """Default path to the user's ``config.toml``."""
    return config_dir() / "config.toml"


def manifest_file() -> Path:
    """Default path to the append-only manifest log."""
    return state_dir() / "manifest.jsonl"


def last_wallpaper() -> Path:
    """Per-user canonical wallpaper copy."""
    return state_dir() / "last_wallpaper.jpg"


def last_config_copy() -> Path:
    """Snapshot of the last successfully applied config."""
    return state_dir() / "last_config.toml"


def templates_dir() -> Path:
    """Per-user pristine QML templates."""
    return state_dir() / "templates"


def shared_wallpapers_dir() -> Path:
    """Return the plasmalogin-visible shared wallpaper directory.

    Override at runtime with ``$USURFACE_SHARED_DIR``. Default is
    ``/usr/local/share/wallpapers`` which matches the
    plasmalogin-readable location used by the existing setup.
    """
    override = os.environ.get("USURFACE_SHARED_DIR")
    if override:
        return Path(override)
    return Path("/usr/local/share/wallpapers")


def shared_wallpaper() -> Path:
    """Default path to the plasmalogin-visible wallpaper file."""
    return shared_wallpapers_dir() / "last_wallpaper.jpg"
