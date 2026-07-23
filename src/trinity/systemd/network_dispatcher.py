"""NetworkManager dispatcher script for wake-on-network.

When enabled via ``trinity install --wake-network``, a dispatcher script
at ``/etc/NetworkManager/dispatcher.d/99-trinity-wake`` runs
``trinity apply --if-changed`` when the network comes up.

The script is non-blocking (``&``), ignores all events except ``up``,
and runs as the invoking user (not root) so the wallpaper refresh
writes to user-owned state files correctly.

This feature is hardware-dependent and opt-in: it is never installed
by default.
"""

from __future__ import annotations

import os
from pathlib import Path

# The dispatcher script template.  NetworkManager passes the interface
# name and event type as positional arguments; we only act on "up".
# The script runs ``trinity apply --if-changed`` as the target user via
# ``su -`` so it writes to the user's state files, not root's.
_TEMPLATE = """\
#!/bin/bash
# Managed by trinity — do not edit
# NetworkManager dispatcher: run trinity apply when Wi-Fi reconnects.
IFACE="$1"
EVENT="$2"
[ "$EVENT" = "up" ] || exit 0
su - {username} -c "trinity apply --if-changed" >/dev/null 2>&1 &
"""

# Default installation path for the dispatcher script.
DISPATCHER_DIR = Path("/etc/NetworkManager/dispatcher.d")
DISPATCHER_PATH = DISPATCHER_DIR / "99-trinity-wake"


def install_network_dispatcher_script(
    username: str, *, dest_path: Path | None = None
) -> Path:
    """Write the NetworkManager dispatcher script.

    The script is owned by root:root with mode 0755 (executable).
    Returns the installed path.

    Raises ``PermissionError`` if the dispatcher directory cannot be
    written (e.g. not running as root).
    """
    import re
    if not re.match(r"^[a-z_][a-z0-9_-]*$", username, re.IGNORECASE):
        raise ValueError(f"invalid username: {username}")
    dest = dest_path or DISPATCHER_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = _TEMPLATE.format(username=username)
    dest.write_text(content, encoding="utf-8")
    # chown to root:root — best-effort: in tests/unprivileged contexts
    # chown may fail (EPERM). The mode is the important security boundary.
    if os.geteuid() == 0:
        try:
            os.chown(dest, 0, 0)
        except OSError:
            pass
    os.chmod(dest, 0o755)
    return dest


def uninstall_network_dispatcher_script(*, dest_path: Path | None = None) -> bool:
    """Remove the dispatcher script.  Returns True if removed."""
    dest = dest_path or DISPATCHER_PATH
    if not dest.is_file():
        return False
    dest.unlink()
    return True
