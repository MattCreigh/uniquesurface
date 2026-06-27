"""Helpers around the ``kwriteconfig6`` / ``kreadconfig6`` shell-outs.

We shell out only for files Plasma itself writes (``appletsrc``,
``kscreenlockerrc``). For files we own, we write them ourselves via
:mod:`usurface.atomic`.

All subprocess calls support a ``dry_run`` flag so the planner can
preview what would be written without touching the system.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from usurface.logging import get_logger

_log = get_logger(__name__)


class KConfigToolMissing(RuntimeError):
    """Raised when ``kwriteconfig6`` / ``kreadconfig6`` is not on PATH."""


def ensure_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise KConfigToolMissing(
            f"required tool {name!r} not found on PATH; "
            "this tool is provided by plasma6-kdecoration / plasma-desktop"
        )
    return path


def kwriteconfig(
    *,
    file: Path,
    group: str,
    key: str,
    value: str,
    type_: str | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Call ``kwriteconfig6`` to set a key.

    Returns the argv that was (or would have been) invoked, so it can be
    included in dry-run output.
    """
    argv: list[str] = [
        ensure_tool("kwriteconfig6"),
        "--file",
        str(file),
        "--group",
        group,
        "--key",
        key,
    ]
    if type_:
        argv.extend(["--type", type_])
    argv.append(value)
    if dry_run:
        return argv
    _log.info("kwriteconfig", argv=argv)
    subprocess.run(argv, check=True)
    return argv


def qdbus_call(
    *,
    service: str,
    path: str,
    method: str,
    args: Sequence[str] = (),
    dry_run: bool = False,
) -> list[str]:
    """Call ``qdbus6`` to invoke a method.

    Returns the argv for dry-run inspection.
    """
    argv: list[str] = [ensure_tool("qdbus6"), service, path, method, *args]
    if dry_run:
        return argv
    _log.info("qdbus_call", argv=argv)
    subprocess.run(argv, check=False)
    return argv
