"""Append-only undo log for usurface mutations.

Every file the orchestrator writes is recorded as one JSONL entry. The
log is append-only; ``restore()`` walks newest-first and replays the
inverse op (``write`` records restore the previous SHA-256 by writing
back the captured bytes; ``delete`` records remove the file again).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from usurface import paths

EntryOp = Literal["write", "delete"]


@dataclass(frozen=True)
class ManifestEntry:
    """One record in the manifest log."""

    ts: str
    op: EntryOp
    path: str
    prev_sha256: str | None
    new_sha256: str | None
    prev_bytes_path: str | None = None  # path to a snapshot of previous bytes

    def to_json(self) -> str:
        return json.dumps(
            {
                "ts": self.ts,
                "op": self.op,
                "path": self.path,
                "prev_sha256": self.prev_sha256,
                "new_sha256": self.new_sha256,
                "prev_bytes_path": self.prev_bytes_path,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> "ManifestEntry":
        data: dict[str, Any] = json.loads(raw)
        return cls(
            ts=data["ts"],
            op=data["op"],
            path=data["path"],
            prev_sha256=data.get("prev_sha256"),
            new_sha256=data.get("new_sha256"),
            prev_bytes_path=data.get("prev_bytes_path"),
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    """Return sha256 of file contents or ``None`` if file is missing."""
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Manifest:
    """Append-only undo log backed by a JSONL file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else paths.manifest_file()

    def append(
        self,
        *,
        op: EntryOp,
        path: str,
        prev_sha256: str | None,
        new_sha256: str | None,
        prev_bytes_path: str | None = None,
    ) -> ManifestEntry:
        entry = ManifestEntry(
            ts=_now_iso(),
            op=op,
            path=path,
            prev_sha256=prev_sha256,
            new_sha256=new_sha256,
            prev_bytes_path=prev_bytes_path,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")
        return entry

    def iter_entries(self) -> list[ManifestEntry]:
        """Return all entries oldest-first."""
        if not self.path.exists():
            return []
        out: list[ManifestEntry] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(ManifestEntry.from_json(line))
        return out

    def head(self, n: int = 10) -> list[ManifestEntry]:
        return self.iter_entries()[-n:]


def snapshot_previous_bytes(
    manifest: Manifest,
    target: Path,
    snapshots_dir: Path | None = None,
) -> tuple[str, str]:
    """If ``target`` exists, copy it into the snapshots dir.

    Returns ``(prev_sha256, snapshot_path_str)``. If the file does not
    exist, returns ``(None, None)``.
    """
    if not target.exists():
        return None, ""  # type: ignore[return-value]

    snapshots = snapshots_dir or paths.state_dir() / "manifest_snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    sha = sha256_file(target) or ""
    snap_path = snapshots / f"{sha}.bin"
    if not snap_path.exists():
        import shutil

        shutil.copy2(target, snap_path)
    return sha, str(snap_path)


def write_tracked(
    manifest: Manifest,
    target: Path,
    data: bytes,
    *,
    mode: int | None = None,
    snapshots_dir: Path | None = None,
) -> ManifestEntry:
    """Atomic write that also records the operation in the manifest."""
    prev_sha, prev_snap = snapshot_previous_bytes(manifest, target, snapshots_dir)
    from usurface.atomic import atomic_write_bytes

    atomic_write_bytes(target, data, mode=mode)
    new_sha = sha256_bytes(data)
    return manifest.append(
        op="write",
        path=str(target),
        prev_sha256=prev_sha,
        new_sha256=new_sha,
        prev_bytes_path=prev_snap,
    )


def restore(manifest: Manifest, *, to: str | None = None) -> int:
    """Revert every recorded op, newest-first.

    Returns the number of entries restored. Stops at ``to`` (timestamp)
    if provided.
    """
    entries = list(reversed(manifest.iter_entries()))
    if to is not None:
        entries = [e for e in entries if e.ts > to]

    from usurface.atomic import atomic_write_bytes

    count = 0
    for entry in entries:
        target = Path(entry.path)
        if entry.op == "write":
            if entry.prev_bytes_path and Path(entry.prev_bytes_path).exists():
                prev_data = Path(entry.prev_bytes_path).read_bytes()
                atomic_write_bytes(target, prev_data)
                count += 1
            elif entry.prev_sha256 is None:
                # No previous content existed; remove the file we wrote.
                try:
                    target.unlink()
                    count += 1
                except FileNotFoundError:
                    pass
            else:
                # Previous snapshot is missing; cannot restore safely.
                raise FileNotFoundError(
                    f"manifest entry references missing snapshot {entry.prev_bytes_path!r} "
                    f"for {entry.path!r}"
                )
        elif entry.op == "delete":
            if target.exists():
                target.unlink()
                count += 1
    return count


def truncate(manifest: Manifest) -> None:
    """Empty the manifest log. Used after a successful verified restore."""
    if manifest.path.exists():
        manifest.path.unlink()
