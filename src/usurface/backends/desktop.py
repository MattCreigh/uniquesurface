"""Desktop wallpaper backend.

Writes ``~/.config/plasma-org.kde.plasma.desktop-appletsrc`` via
``kwriteconfig6`` and asks Plasma to refresh its wallpaper.
"""

from __future__ import annotations

from pathlib import Path

from usurface.backends import _kconfig
from usurface.manifest import Manifest

_GROUP = "Containments"
_DESKTOP_KEY = "Image"
_PLUGIN_KEY = "wallpaperplugin"
_DEFAULT_PLUGIN = "org.kde.image"


class DesktopBackend:
    name = "desktop"

    def apply(self, manifest: Manifest, wallpaper: Path) -> None:
        file_path = Path("~/.config/plasma-org.kde.plasma.desktop-appletsrc").expanduser()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        uri = wallpaper.resolve().as_uri()
        
        from usurface.manifest import snapshot_previous_bytes, sha256_file
        prev_sha, prev_snap = snapshot_previous_bytes(manifest, file_path)

        # We rely on kwriteconfig6 here so Plasma reads the change in
        # the canonical INI format; this is what Plasma itself writes.
        _kconfig.kwriteconfig(file=file_path, group=_GROUP, key=_PLUGIN_KEY, value=_DEFAULT_PLUGIN)
        _kconfig.kwriteconfig(file=file_path, group=_GROUP, key=_DESKTOP_KEY, value=uri)
        _kconfig.qdbus_call(
            service="org.kde.plasma.desktop",
            path="/PlasmaShell",
            method="refreshWallpaper",
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
        file_path = Path("~/.config/plasma-org.kde.plasma.desktop-appletsrc").expanduser()
        uri = wallpaper.resolve().as_uri()
        plan = [
            f"kwriteconfig6 --file {file_path} --group {_GROUP} --key {_PLUGIN_KEY} {_DEFAULT_PLUGIN}",
            f"kwriteconfig6 --file {file_path} --group {_GROUP} --key {_DESKTOP_KEY} {uri}",
            "qdbus6 org.kde.plasma.desktop /PlasmaShell refreshWallpaper",
        ]
        return plan
