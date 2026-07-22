"""Persisted refresh state for ``trinity apply --if-changed``.

One small JSON document in the user state dir records what the last
successful apply produced: a fingerprint of the configured source, the
provider's change token (when the provider can probe), and the digest
of the verified image bytes.  The hourly systemd timer uses it to turn
most runs into a single metadata-sized HTTP request instead of a full
download + surface rewrite.

Loading is deliberately tolerant: a missing, corrupt, or
schema-incompatible state file degrades ``--if-changed`` to a full
apply — it must never block the refresh.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 1

# Filename inside ``surface.behaviour.user_dir``.
STATE_FILENAME = "refresh_state.json"


@dataclass(frozen=True)
class RefreshState:
    """What the last successful apply produced."""

    # sha256 over the canonical provider name + options; a config change
    # invalidates all cached tokens/digests.
    fingerprint: str
    # Provider change token from trinity_provider_probe, or None when the
    # provider cannot probe (token comparison is then skipped).
    probe_token: str | None
    # sha256 of the verified (Pillow re-encoded) image bytes.
    image_sha256: str
    # The shared wallpaper file the surfaces point at.
    wallpaper_path: str
    # ISO 8601 UTC timestamp of the apply.
    applied_at: str
    # Temporal offset for cyclical provisioning (trinity cycle).
    # 0 = current day, 1 = yesterday, … 6 = 6 days ago.
    # Defaults to 0 for backward compatibility with older state files.
    temporal_offset: int = 0


def source_fingerprint(provider: str, options: dict[str, Any]) -> str:
    """Digest of the configured source; changes when the config does."""
    canonical = json.dumps(
        {"provider": provider, "options": options}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cycle_token(provider: str, options: dict[str, Any], offset: int) -> str:
    """Compound token combining the provider fingerprint + temporal offset.

    The ``--if-changed`` timer uses this so a manual ``trinity cycle``
    (which changes the offset but not the config) is not clobbered by
    the next hourly run — the compound token differs until the user
    cycles again or the upstream master image changes.
    """
    base = source_fingerprint(provider, options)
    return f"{base}:{offset}"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load(path: Path) -> RefreshState | None:
    """Load state from ``path``; ``None`` on any problem (fail open)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != _SCHEMA_VERSION:
        return None
    try:
        token = raw["probe_token"]
        return RefreshState(
            fingerprint=str(raw["fingerprint"]),
            probe_token=str(token) if token is not None else None,
            image_sha256=str(raw["image_sha256"]),
            wallpaper_path=str(raw["wallpaper_path"]),
            applied_at=str(raw["applied_at"]),
            temporal_offset=int(raw.get("temporal_offset", 0)),
        )
    except KeyError:
        return None


def save(path: Path, state: RefreshState) -> None:
    """Atomically write ``state`` to ``path``."""
    from trinity.atomic import atomic_write_text

    payload: dict[str, Any] = {"schema": _SCHEMA_VERSION, **asdict(state)}
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n", mode=0o644)
