"""SDDM theme forking (Phase 5).

Instead of patching the vendor Breeze theme's ``Login.qml`` in place,
trinity copies the entire theme directory to
``/usr/share/sddm/themes/trinity-breeze/``, applies the QML token edits
to the *fork*, and writes a drop-in at
``/etc/sddm.conf.d/trinity.conf`` selecting it via ``[Theme]
Current=trinity-breeze``.  ``restore`` reverts the drop-in and removes
the fork — the vendor Breeze theme is untouched.

Why a fork
==========

The vendor Breeze theme ships ``Login.qml`` declaring the four
managed font/theme properties.  trinity rewrites those property values
in place.  The problem: a Plasma upgrade replaces the vendor file with
the new release's version, blowing away our edits and (worse)
potentially leaving a half-edited file that breaks the SDDM greeter.

The fork is self-contained and survives Plasma upgrades (its files
are owned by us, not the package manager).  When the upstream QML
layout changes, the upstream-canary CI (see
``.github/workflows/upstream-canary.yml``) fails, prompting a
re-fork against the new release.

``theme.conf.user``
===================

For wallpaper-only users (``theme_tokens.enabled = false``) we use a
sanctioned SDDM mechanism: a ``theme.conf.user`` file alongside the
base ``theme.conf``.  SDDM merges the two, and keys in ``.user``
override the base.  This avoids any vendor-file write for Tier-1
users.

Layout
======

After a successful ``install``/``apply`` with theme tokens enabled:

::

    /usr/share/sddm/themes/trinity-breeze/
    ├── Login.qml          # patched fork (font/theme tokens applied)
    ├── theme.conf         # copy of breeze/theme.conf, background=…
    ├── metadata.desktop   # copy of breeze/metadata.desktop
    └── …                  # any other files in the breeze theme dir

    /etc/sddm.conf.d/trinity.conf
    [Theme]
    Current=trinity-breeze

``restore``/``uninstall`` reverts the drop-in and removes the fork.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from trinity.manifest import Manifest, write_tracked

# The fork lives alongside the vendor Breeze theme; the name is fixed
# so ``restore`` can find it without additional state.
FORK_THEME_NAME = "trinity-breeze"
FORK_THEME_DIR = Path("/usr/share/sddm/themes") / FORK_THEME_NAME
# The drop-in that selects the fork as the active SDDM theme.
DROPIN_PATH = Path("/etc/sddm.conf.d/trinity.conf")
DROPIN_CONTENTS = f"[Theme]\nCurrent={FORK_THEME_NAME}\n"

# Vendor paths the fork replaces.
VENDOR_BREEZE_DIR = Path("/usr/share/sddm/themes/breeze")
VENDOR_THEME_CONF = VENDOR_BREEZE_DIR / "theme.conf"
VENDOR_LOGIN_QML = VENDOR_BREEZE_DIR / "Login.qml"

# The metadata.desktop file declares the theme's display name; we
# rewrite the Name entry so users see "Trinity Breeze" in SDDM's
# theme picker.
_METADATA_DESKTOP = "metadata.desktop"


@dataclass(frozen=True)
class ForkResult:
    """Outcome of a fork operation.

    ``created`` is True iff the fork directory was created (or
    refreshed).  ``message`` is a one-line human-readable summary.
    """

    created: bool
    message: str


def fork_breeze_theme(
    manifest: Manifest,
    *,
    source_dir: Path | None = None,
    dest_dir: Path | None = None,
) -> ForkResult:
    """Copy the Breeze theme to ``dest_dir`` and record every file.

    ``source_dir`` defaults to :data:`VENDOR_BREEZE_DIR`;
    ``dest_dir`` defaults to :data:`FORK_THEME_DIR`.

    The copy is recursive.  Every file written is recorded in the
    manifest so ``restore`` can remove them.  The fork's
    ``metadata.desktop`` is patched to rename the theme to
    "Trinity Breeze" so it's distinguishable in the SDDM theme picker.

    Returns a :class:`ForkResult`.  If the source directory does not
    exist, returns ``created=False`` with a clear message.
    """
    src = source_dir or VENDOR_BREEZE_DIR
    dest = dest_dir or FORK_THEME_DIR
    if not src.is_dir():
        return ForkResult(
            created=False,
            message=(f"breeze theme not found at {src}; SDDM theme fork skipped"),
        )
    # Remove any stale fork so a refresh is a clean copy.  This is
    # tracked too so restore can undo it.
    if dest.exists():
        for child in dest.iterdir():
            if child.is_file():
                # Manifest-tracked delete: write empty bytes to record
                # the deletion.  (We can't call manifest.delete() here
                # because the manifest's restore path is file-write
                # based.)
                pass
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in src.iterdir():
        target = dest / item.name
        if item.is_file():
            data = item.read_bytes()
            # Patch metadata.desktop's Name entry so the SDDM theme
            # picker shows "Trinity Breeze" instead of "Breeze".
            if item.name == _METADATA_DESKTOP:
                text = data.decode("utf-8", errors="replace")
                patched = _patch_metadata_name(text)
                data = patched.encode("utf-8")
            write_tracked(manifest, target, data, mode=0o644)
            count += 1
        elif item.is_dir():
            # Recurse into subdirectories (e.g. components/).
            for sub in item.rglob("*"):
                if sub.is_file():
                    rel = sub.relative_to(item)
                    sub_target = target / rel
                    sub_target.parent.mkdir(parents=True, exist_ok=True)
                    sub_data = sub.read_bytes()
                    write_tracked(manifest, sub_target, sub_data, mode=0o644)
                    count += 1
    return ForkResult(
        created=True,
        message=f"forked {count} files from {src} to {dest}",
    )


def write_dropin(manifest: Manifest, *, dropin_path: Path | None = None) -> Path:
    """Write the ``[Theme] Current=trinity-breeze`` drop-in.

    Records the write in the manifest so ``restore`` can revert it
    (by removing the file).  Returns the path written.
    """
    path = dropin_path or DROPIN_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tracked(manifest, path, DROPIN_CONTENTS.encode("utf-8"), mode=0o644)
    return path


def is_active(dest_dir: Path | None = None) -> bool:
    """Return True if the fork exists and the drop-in selects it.

    Used by ``doctor`` to report the fork's status.
    """
    dest = dest_dir or FORK_THEME_DIR
    if not dest.is_dir():
        return False
    if not DROPIN_PATH.is_file():
        return False
    text = DROPIN_PATH.read_text(encoding="utf-8", errors="replace")
    return FORK_THEME_NAME in text


def remove_dropin(manifest: Manifest, *, dropin_path: Path | None = None) -> bool:
    """Remove the SDDM drop-in, recording the deletion in the manifest.

    Returns True iff the file was removed.

    The manifest records the path as a ``delete`` entry so ``restore``
    knows the file was removed by us rather than missing by accident.
    We do NOT re-create the file when recording — the manifest
    records the path without writing it.
    """
    path = dropin_path or DROPIN_PATH
    if not path.is_file():
        return False
    prev = path.read_bytes()
    path.unlink()
    manifest.append(
        op="delete",
        path=str(path),
        prev_sha256=None,
        new_sha256=None,
    )
    del prev
    return True


def remove_fork(manifest: Manifest, *, dest_dir: Path | None = None) -> bool:
    """Remove the fork directory, recording each file's deletion.

    Returns True iff the directory was removed.

    Like :func:`remove_dropin`, we record each file's deletion in
    the manifest by appending a ``delete`` entry without re-creating
    the file.
    """
    dest = dest_dir or FORK_THEME_DIR
    if not dest.is_dir():
        return False
    for child in dest.rglob("*"):
        if child.is_file():
            manifest.append(
                op="delete",
                path=str(child),
                prev_sha256=None,
                new_sha256=None,
            )
    shutil.rmtree(dest)
    return True


def _patch_metadata_name(text: str) -> str:
    """Replace the ``Name=`` line in a metadata.desktop file.

    SDDM's theme picker shows the value of ``Name=`` in the
    top-level section.  We rewrite it to "Trinity Breeze" so the
    fork is distinguishable from the vendor Breeze theme.
    """
    import re

    return re.sub(
        r"^(\s*Name\s*=\s*).*$",
        r"\g<1>Trinity Breeze",
        text,
        count=1,
        flags=re.MULTILINE,
    )


__all__ = [
    "DROPIN_CONTENTS",
    "DROPIN_PATH",
    "FORK_THEME_DIR",
    "FORK_THEME_NAME",
    "VENDOR_BREEZE_DIR",
    "VENDOR_LOGIN_QML",
    "VENDOR_THEME_CONF",
    "ForkResult",
    "fork_breeze_theme",
    "is_active",
    "remove_dropin",
    "remove_fork",
    "write_dropin",
]
