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

from pathlib import Path

from trinity import paths as _paths
from trinity.backends import _kconfig
from trinity.backends.base import BackendError
from trinity.logging_setup import get_logger
from trinity.manifest import Manifest

_log = get_logger(__name__)

_GROUP = "Greeter"
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

        from trinity.manifest import sha256_file, snapshot_previous_bytes

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
            # Top-level Image= (used by some greeter versions).
            _kconfig.kwriteconfig(
                file=file_path, group=_GROUP, key=_IMAGE_KEY, value=uri
            )
            # Nested group: [Greeter][Wallpaper][org.kde.image][General]
            # This is what the org.kde.image plugin actually reads.
            _kconfig.kwriteconfig_nested(
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
                "qdbus6_missing",
                hint="lock screen will reload on next lock",
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
        nested_args = " ".join(f"--group {g}" for g in _NESTED_GROUP)
        return [
            f"kwriteconfig6 --file {file_path} --group {_GROUP} "
            f"--key {_WALLPAPER_KEY} {_PLUGIN_VALUE}",
            f"kwriteconfig6 --file {file_path} "
            f"--group {_GROUP} --key {_IMAGE_KEY} {uri}",
            f"kwriteconfig6 --file {file_path} {nested_args} --key {_IMAGE_KEY} {uri}",
            "qdbus6 org.freedesktop.ScreenSaver /org/freedesktop/ScreenSaver "
            "org.kde.screensaver.configure",
        ]
