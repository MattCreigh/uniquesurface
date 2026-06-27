"""Lock-screen wallpaper backend.

Writes ``~/.config/kscreenlockerrc`` so ``kscreenlocker_greet`` uses the
configured image plugin with our wallpaper path.
"""

from __future__ import annotations

from pathlib import Path

from usurface.backends import _kconfig
from usurface.manifest import Manifest

_GROUP = "Greeter"
_PLUGIN_KEY = "Theme"
_PLUGIN_VALUE = "org.kde.image"
_IMAGE_KEY = "Image"


class LockBackend:
    name = "lock"

    def apply(self, manifest: Manifest, wallpaper: Path) -> None:
        file_path = Path("~/.config/kscreenlockerrc").expanduser()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        uri = wallpaper.resolve().as_uri()
        _kconfig.kwriteconfig(file=file_path, group=_GROUP, key=_PLUGIN_KEY, value=_PLUGIN_VALUE)
        _kconfig.kwriteconfig(file=file_path, group=_GROUP, key=_IMAGE_KEY, value=uri)

    def dry_run_plan(self, wallpaper: Path) -> list[str]:
        file_path = Path("~/.config/kscreenlockerrc").expanduser()
        uri = wallpaper.resolve().as_uri()
        return [
            f"kwriteconfig6 --file {file_path} --group {_GROUP} --key {_PLUGIN_KEY} {_PLUGIN_VALUE}",
            f"kwriteconfig6 --file {file_path} --group {_GROUP} --key {_IMAGE_KEY} {uri}",
        ]
