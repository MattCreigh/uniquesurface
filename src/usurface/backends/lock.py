"""Lock-screen wallpaper backend.

Writes ``~/.config/kscreenlockerrc`` so ``kscreenlocker_greet`` uses the
configured image plugin with our wallpaper path.

The kscreenlocker 6.x source defines the relevant config keys in
``settings/kscreenlockersettings.kcfg``:

  [Greeter]
  Theme=<theme name>
  WallpaperPlugin=<plugin id>   # this is the INI key for wallpaperPluginId

The greeter then loads the wallpaper plugin and reads its config from
``[Greeter][Wallpaper][<WallpaperPlugin>]`` (e.g.
``[Greeter][Wallpaper][org.kde.image]``). The ``org.kde.image`` plugin
defines its own ``[General]`` sub-group in ``contents/config/main.xml``,
so the full key path for the image is
``[Greeter][Wallpaper][org.kde.image][General] Image=``.

We write all of these so the wallpaper is picked up across greeter
versions.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from usurface import paths as _paths
from usurface.backends import _kconfig
from usurface.backends.base import BackendError
from usurface.manifest import Manifest

_log = _kconfig._log

_GROUP = "Greeter"
_PLUGIN_KEY = "Theme"  # legacy (unused in Plasma 6)
_WALLPAPER_KEY = "WallpaperPlugin"  # the INI key for wallpaperPluginId
_PLUGIN_VALUE = "org.kde.image"
_IMAGE_KEY = "Image"

# Nested group that org.kde.image reads from:
# [Greeter][Wallpaper][org.kde.image][General]
# The kscreenlocker greeter (greeterapp.cpp createViewForScreen) reads the
# wallpaper plugin config from group("Greeter").group("Wallpaper").group(<pluginId>)
# and the org.kde.image plugin adds its own [General] subgroup holding Image=.
# This is the canonical, correct path — confirmed against upstream source.
_NESTED_GROUP = [_GROUP, "Wallpaper", _PLUGIN_VALUE, "General"]


class LockBackend:
    name = "lock"

    def apply(self, manifest: Manifest, wallpaper: Path) -> None:
        file_path = _paths.config_dir().parent / "kscreenlockerrc"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        uri = wallpaper.resolve().as_uri()

        from usurface.manifest import snapshot_previous_bytes, sha256_file

        prev_sha, prev_snap = snapshot_previous_bytes(manifest, file_path)

        try:
            # The kcfg maps the C++ property ``wallpaperPluginId`` to the
            # INI key ``WallpaperPlugin``. kwriteconfig6 writes the
            # string verbatim, so we write the INI key name explicitly.
            _kconfig.kwriteconfig(
                file=file_path,
                group=_GROUP,
                key=_WALLPAPER_KEY,
                value=_PLUGIN_VALUE,
            )
            # Legacy Theme= key (unused in Plasma 6 but harmless).
            _kconfig.kwriteconfig(
                file=file_path,
                group=_GROUP,
                key=_PLUGIN_KEY,
                value="",  # empty: no legacy theme
            )
            # Top-level Image= (used by some greeter versions).
            _kconfig.kwriteconfig(
                file=file_path, group=_GROUP, key=_IMAGE_KEY, value=uri
            )
            # Nested group: [Greeter][Wallpaper][org.kde.image][General]
            # This is what the org.kde.image plugin actually reads.
            _kwriteconfig_nested(
                file=file_path,
                group_path=_NESTED_GROUP,
                key=_IMAGE_KEY,
                value=uri,
            )
        except (_kconfig.KConfigToolMissing, FileNotFoundError, OSError) as exc:
            raise BackendError(
                f"failed to update lock-screen config: {exc}",
                hint=(
                    "install plasma-workspace (provides kwriteconfig6) and ensure "
                    "the screen locker is configured."
                ),
            ) from exc

        # Ask the running kscreenlocker to re-read its config so the
        # *next* lock uses the new wallpaper, without needing to lock
        # and unlock first. Best-effort: a no-op if the ScreenSaver
        # service is not on the bus (headless / not yet running).
        try:
            _kconfig.reload_lockscreen_config()
        except _kconfig.KConfigToolMissing:
            _log.warning(
                "qdbus6 not available; lock screen will reload on next lock"
            )

        new_sha = sha256_file(file_path)
        manifest.append(
            op="write",
            path=str(file_path),
            prev_sha256=prev_sha,
            new_sha256=new_sha,
            prev_bytes_path=prev_snap,
        )

    def dry_run_plan(self, wallpaper: Path) -> list[str]:
        file_path = _paths.config_dir().parent / "kscreenlockerrc"
        uri = wallpaper.resolve().as_uri()
        nested = "\\".join(_NESTED_GROUP)
        return [
            f"kwriteconfig6 --file {file_path} --group {_GROUP} --key {_WALLPAPER_KEY} {_PLUGIN_VALUE}",
            f"kwriteconfig6 --file {file_path} --group {_GROUP} --key {_IMAGE_KEY} {uri}",
            f"kwriteconfig6 --file {file_path} --group {nested} --key {_IMAGE_KEY} {uri}",
            "qdbus6 org.freedesktop.ScreenSaver /org/freedesktop/ScreenSaver org.kde.screensaver.configure",
        ]


def _kwriteconfig_nested(
    *, file: Path, group_path: list[str], key: str, value: str
) -> None:
    """Call ``kwriteconfig6`` with a nested group path.

    kwriteconfig6 takes ``--group`` once per nesting level. This is
    the format that produces ``[A][B][C]`` in the file.
    """
    if not group_path:
        raise ValueError("group_path must not be empty")
    binary = _kconfig.ensure_tool("kwriteconfig6")
    argv: list[str] = [binary, "--file", str(file)]
    for g in group_path:
        argv.extend(["--group", g])
    argv.extend(["--key", key, "--type", "string", value])
    _log.info("kwriteconfig_nested", argv=argv)
    if os.geteuid() == 0:
        # When running as root, drop to the invoking user so we don't
        # accidentally write root-owned lock-screen config.
        sudo_user = os.environ.get("SUDO_USER")
        sudo_uid = os.environ.get("SUDO_UID")
        if sudo_user and sudo_uid:
            argv = [
                "sudo",
                "-u",
                sudo_user,
                "env",
                f"XDG_RUNTIME_DIR=/run/user/{sudo_uid}",
                *argv,
            ]
    subprocess.run(argv, check=True)
