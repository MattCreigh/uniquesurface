"""Backend writers for the three Plasma surfaces.

Each backend knows how to apply a wallpaper (and any associated
options) to one of the three surfaces and is otherwise side-effect
free. The orchestrator wires backends together at apply time.
"""

from __future__ import annotations
