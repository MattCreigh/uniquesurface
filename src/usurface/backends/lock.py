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

        from usurface.manifest import snapshot_previous_bytes, sha256_file
        prev_sha, prev_snap = snapshot_previous_bytes(manifest, file_path)

        _kconfig.kwriteconfig(file=file_path, group=_GROUP, key=_PLUGIN_KEY, value=_PLUGIN_VALUE)
        _kconfig.kwriteconfig(file=file_path, group=_GROUP, key=_IMAGE_KEY, value=uri)

        new_sha = sha256_file(file_path)
        manifest.append(
            op="write",
            path=str(file_path),
            prev_sha256=prev_sha,
            new_sha256=new_sha,
            prev_bytes_path=prev_snap,
        )


    def dry_run_plan(self, wallpaper: Path) -> list[str]:
        file_path = Path("~/.config/kscreenlockerrc").expanduser()
        uri = wallpaper.resolve().as_uri()
        return [
            f"kwriteconfig6 --file {file_path} --group {_GROUP} --key {_PLUGIN_KEY} {_PLUGIN_VALUE}",
            f"kwriteconfig6 --file {file_path} --group {_GROUP} --key {_IMAGE_KEY} {uri}",
        ]
