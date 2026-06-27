"""Protocol shared by all backends."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from usurface.manifest import Manifest


@runtime_checkable
class Backend(Protocol):
    """A surface writer.

    ``name`` is a short identifier (e.g. ``"desktop"``). ``apply`` writes
    ``wallpaper`` to its target surface. ``dry_run_plan`` returns the
    human-readable summary of what ``apply`` would do.
    """

    name: str

    def apply(self, manifest: Manifest, wallpaper: Path) -> None: ...

    def dry_run_plan(self, wallpaper: Path) -> list[str]:
        """Return a list of human-readable lines describing the plan."""
        ...
