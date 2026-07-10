"""Install the bundled Inter font into a system-wide fonts directory.

The default target is ``/usr/local/share/fonts/trinity/`` which is
readable by SDDM. If the target is not writable, the install prints a
clear warning and falls back to the user-local fonts directory
(``~/.local/share/fonts/``) so the user can still benefit from Inter
on the desktop; the login screen will keep the system default font.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

_SYSTEM_TARGET = Path("/usr/local/share/fonts/trinity")


def _user_target() -> Path:
    """User-local fonts dir, expanded per-call so ``HOME`` changes
    (sudo, test isolation) are honoured."""
    return Path("~/.local/share/fonts").expanduser()


@dataclass(frozen=True)
class FontInstallResult:
    installed_to: Path
    system_wide: bool
    ran_fc_cache: bool
    used_source: Path


def _bundled_font() -> Path | None:
    """Return the path to the bundled Inter font, or None if absent."""
    from importlib.resources import files

    pkg_root = files("trinity")
    for name in (
        "Inter-Regular.ttf",
        "fonts/Inter-Regular.ttf",
        "theme/fonts/Inter-Regular.ttf",
    ):
        candidate = pkg_root.joinpath(name)
        if candidate.is_file():
            return Path(str(candidate))
    return None


def _system_writable() -> bool:
    """Return True if /usr/local/share/fonts/trinity is creatable as root."""
    if os.geteuid() == 0:
        return True
    return os.access("/usr/local/share", os.W_OK)


def _run_fc_cache(target: Path) -> bool:
    """Best-effort ``fc-cache -f`` for ``target``."""
    fc_cache = shutil.which("fc-cache")
    if not fc_cache:
        return False
    import subprocess

    subprocess.run([fc_cache, "-f", str(target)], check=False, timeout=120.0)
    return True


def install(
    *, source: Path | None = None, force_user: bool = False
) -> FontInstallResult:
    """Copy the Inter font into the appropriate fonts directory.

    Parameters
    ----------
    source:
        Override path to a TTF. If absent, the bundled font is used.
    force_user:
        Force the user-local fallback even if system-wide would work.
    """
    src = source or _bundled_font()
    if src is None or not src.is_file():
        raise FileNotFoundError(
            "No bundled Inter font found in the package and "
            "no source= override provided. Install Inter system-wide "
            "(e.g. fonts-inter) or provide a TTF via source=."
        )

    if not force_user and _system_writable():
        target_dir = _SYSTEM_TARGET
        system_wide = True
    else:
        target_dir = _user_target()
        system_wide = False

    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / src.name
    shutil.copy2(src, dest)
    ran_fc_cache = _run_fc_cache(target_dir)

    return FontInstallResult(
        installed_to=dest,
        system_wide=system_wide,
        ran_fc_cache=ran_fc_cache,
        used_source=src,
    )


def is_installed(family: str = "Inter") -> bool:
    """Best-effort check whether ``family`` resolves via fontconfig.

    Uses ``fc-match --format`` to print only the resolved family name,
    so the match is exact rather than a substring of the full fc-match
    line (which previously let "Inter" match "Inter Dimensional").
    """
    fc_match = shutil.which("fc-match")
    if not fc_match:
        return False
    import subprocess

    out = subprocess.run(
        [fc_match, family, "--format", "%{family}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5.0,
    )
    # fc-match -f "%{family}" prints the resolved family for the query.
    # The result can be a comma-separated list (e.g. "Inter,DejaVu Sans");
    # we accept the query if *any* of the resolved families matches the
    # wanted name, so a fallback doesn't cause a false negative.
    resolved_families = [
        f.strip().lower() for f in out.stdout.strip().split(",") if f.strip()
    ]
    wanted = family.strip().split()[0].lower()
    return bool(resolved_families) and wanted in resolved_families
