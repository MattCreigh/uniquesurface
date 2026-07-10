"""Desktop wallpaper backend.

Writes ``~/.config/plasma-org.kde.plasma.desktop-appletsrc`` via
``kwriteconfig6`` and asks the running Plasma shell to apply the new
wallpaper *live* (without a visible reload/flip).

The real desktop wallpaper lives at::

    [Containments][<id>][Wallpaper][org.kde.image][General]
    Image=<file:// URI>

where ``<id>`` is the numeric containment id of each desktop containment.
Plasma ignores the flat ``[Containments]`` group for wallpaper purposes,
so we must discover each real containment id at runtime by parsing the
appletsrc file (kwriteconfig6 has no list-groups verb). We write the
nested group for every desktop containment so multi-screen / multi-activity
setups all update.

For the live update we call the PlasmaShell ``evaluateScript`` D-Bus
method (service ``org.kde.plasmashell``), which iterates every desktop
containment and writes ``Image`` through the same path Plasma's own
settings UI uses — the swap is atomic and invisible to the user.
"""

from __future__ import annotations

import re
from pathlib import Path

from trinity import paths as _paths
from trinity.backends import _kconfig
from trinity.backends.base import BackendError
from trinity.logging import get_logger
from trinity.manifest import Manifest

_log = get_logger(__name__)

_APPLET_FILE = "plasma-org.kde.plasma.desktop-appletsrc"
_DESKTOP_KEY = "Image"
_PLUGIN_KEY = "wallpaperplugin"
_DEFAULT_PLUGIN = "org.kde.image"
_WALLPAPER_SUBGROUPS = ["Wallpaper", _DEFAULT_PLUGIN, "General"]

# Matches ``[Containments][<id>]`` header lines in the appletsrc.
_CONTAINMENT_RE = re.compile(r"^\[Containments\]\[(\d+)\]$")


def _appletsrc_path() -> Path:
    return _paths.config_dir().parent / _APPLET_FILE


def _discover_desktop_containments(appletsrc: Path) -> list[int]:
    """Return the numeric ids of every containment that declares a wallpaperplugin.

    A containment is a top-level ``[Containments][<id>]`` group whose
    ``wallpaperplugin=`` key is set. Panels and other non-desktop
    containments also carry ``wallpaperplugin``, so we include every
    containment that declares one — the live ``evaluateScript`` call
    targets only ``desktops()`` and so is naturally scoped. If none
    match (a fresh install with no appletsrc yet) we return an empty
    list and the live D-Bus script handles the apply.
    """
    if not appletsrc.is_file():
        return []
    ids: list[int] = []
    current_id: int | None = None
    has_wallpaper_plugin = False
    for line in appletsrc.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _CONTAINMENT_RE.match(line)
        if m:
            # Flush the previous containment if it had a wallpaperplugin.
            if current_id is not None and has_wallpaper_plugin:
                ids.append(current_id)
            current_id = int(m.group(1))
            has_wallpaper_plugin = False
            continue
        if current_id is not None and line.startswith("["):
            # New subgroup; the wallpaperplugin= key, if present, was on
            # the containment header block which we've now left.
            if has_wallpaper_plugin and current_id not in ids:
                ids.append(current_id)
            current_id = None
            has_wallpaper_plugin = False
            continue
        if current_id is not None and line.startswith(f"{_PLUGIN_KEY}="):
            has_wallpaper_plugin = True
    # Flush the last containment.
    if current_id is not None and has_wallpaper_plugin and current_id not in ids:
        ids.append(current_id)
    return ids


class DesktopBackend:
    name = "desktop"

    def apply(self, manifest: Manifest, wallpaper: Path) -> None:
        file_path = _appletsrc_path()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        uri = wallpaper.resolve().as_uri()

        from trinity.manifest import sha256_file, snapshot_previous_bytes

        prev_sha, prev_snap = snapshot_previous_bytes(manifest, file_path)

        # Write the wallpaper into the *real* containment group(s).
        # Plasma ignores the flat ``[Containments]`` group for wallpaper, and
        # writing it can trigger a config-file reload that briefly resets the
        # desktop to the default image, so we NEVER write the flat group — only
        # the nested ``[Containments][<id>][Wallpaper][org.kde.image][General]``
        # Image= key for each discovered desktop containment.
        try:
            containment_ids = _discover_desktop_containments(file_path)
            for cid in containment_ids:
                group_path = [
                    "Containments",
                    str(cid),
                    *_WALLPAPER_SUBGROUPS,
                ]
                _kconfig.kwriteconfig_nested(
                    file=file_path,
                    group_path=group_path,
                    key=_DESKTOP_KEY,
                    value=uri,
                )
        except (_kconfig.KConfigToolMissing, FileNotFoundError, OSError) as exc:
            raise BackendError(
                f"failed to update Plasma desktop config: {exc}",
                hint=(
                    "install plasma-desktop (provides kwriteconfig6) and ensure "
                    "Plasma is running."
                ),
            ) from exc

        # Apply the new wallpaper *live* via the PlasmaShell evaluateScript
        # D-Bus method. This writes the same nested Image key through the
        # running shell and swaps the wallpaper atomically — no visible
        # reload/flip. Best-effort: if Plasma isn't running, the config
        # file has already been updated and the wallpaper applies on next
        # start.
        try:
            _kconfig.evaluate_wallpaper_script(image_uri=uri, plugin=_DEFAULT_PLUGIN)
        except _kconfig.KConfigToolMissing:
            _log.warning(
                "qdbus6_missing",
                hint="desktop wallpaper will apply on next Plasma start",
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
        file_path = _appletsrc_path()
        uri = wallpaper.resolve().as_uri()
        ids = _discover_desktop_containments(file_path)
        plan: list[str] = []
        for cid in ids:
            group_path = ["Containments", str(cid), *_WALLPAPER_SUBGROUPS]
            group_args = " ".join(f"--group {g}" for g in group_path)
            plan.append(
                f"kwriteconfig6 --file {file_path} {group_args} "
                f"--key {_DESKTOP_KEY} {uri}"
            )
        plan.append(
            f"qdbus6 org.kde.plasmashell /PlasmaShell evaluateScript "
            f"<set Image={uri} on all desktops>"
        )
        return plan
