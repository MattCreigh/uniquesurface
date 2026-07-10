"""Append-only undo log for trinity mutations.

Every file the orchestrator writes is recorded as one JSONL entry. The
log is append-only; ``restore()`` walks newest-first and replays the
inverse op (``write`` records restore the previous SHA-256 by writing
back the captured bytes; ``delete`` records remove the file again).

Undo history is bounded: after a successful ``apply``, the log is
compacted to the most recent ``_RETENTION_ENTRIES`` entries (see
:func:`compact`), and snapshots no longer referenced by any surviving
entry are pruned. A full ``restore`` empties the log and prunes every
snapshot; a partial ``restore --to <ts>`` keeps entries with
``ts <= to`` and prunes only the snapshots those dropped entries
referenced.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from trinity import paths
from trinity.logging import get_logger

_log = get_logger(__name__)

EntryOp = Literal["write", "delete"]

# Maximum number of manifest entries retained after a compaction. Older
# entries beyond this threshold are dropped along with their snapshots,
# bounding undo history so the log and snapshot dir cannot grow forever
# under the daily systemd timer.
_RETENTION_ENTRIES = 200


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
    def from_json(cls, raw: str) -> ManifestEntry:
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

        # Append with a single ``O_APPEND | O_CREAT`` write. The kernel
        # guarantees the seek-to-end + write is atomic for small writes
        # (PIPE_BUF / one page) and the file is opened in append mode so
        # a concurrent writer cannot interleave within our single line.
        # This replaces the previous read-modify-rename pattern, which
        # was O(N) per append and had a non-atomic window where two
        # concurrent ``trinity apply`` invocations could clobber each
        # other's entries.
        line = entry.to_json().encode("utf-8") + b"\n"
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        try:
            fd = os.open(self.path, flags, 0o644)
        except PermissionError as exc:
            raise PermissionError(
                f"Cannot write manifest {self.path}: permission denied. "
                "If you previously ran trinity with sudo, fix ownership with:\n"
                f"  sudo chown -R $USER:$USER {self.path.parent}"
            ) from exc
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        return entry

    def iter_entries(self) -> list[ManifestEntry]:
        """Return all entries oldest-first.

        Skips and logs unparseable lines so a single corrupted line
        (e.g. from a partial write during a crash) does not prevent
        ``status`` / ``restore`` / ``apply`` from working with the
        remaining valid entries.
        """
        if not self.path.exists():
            return []
        out: list[ManifestEntry] = []
        with self.path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(ManifestEntry.from_json(line))
                except (json.JSONDecodeError, KeyError) as exc:
                    _log.warning(
                        "manifest_corrupt_line_skipped",
                        path=str(self.path),
                        lineno=lineno,
                        error=str(exc),
                    )
        return out

    def head(self, n: int = 10) -> list[ManifestEntry]:
        return self.iter_entries()[-n:]


def snapshot_previous_bytes(
    manifest: Manifest,
    target: Path,
    snapshots_dir: Path | None = None,
) -> tuple[str | None, str | None]:
    """If ``target`` exists, copy it into the snapshots dir.

    Returns ``(prev_sha256, snapshot_path_str)``. If the file does not
    exist, returns ``(None, None)``.
    """
    if not target.exists():
        return None, None

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
    from trinity.atomic import atomic_write_bytes

    atomic_write_bytes(target, data, mode=mode)
    new_sha = sha256_bytes(data)
    return manifest.append(
        op="write",
        path=str(target),
        prev_sha256=prev_sha,
        new_sha256=new_sha,
        prev_bytes_path=prev_snap,
    )


def restore(
    manifest: Manifest,
    *,
    to: str | None = None,
    snapshots_dir: Path | None = None,
) -> int:
    """Revert every recorded op, newest-first.

    Returns the number of entries restored. Stops at ``to`` (timestamp)
    if provided — entries with ``ts > to`` are reverted, entries with
    ``ts <= to`` are kept.

    On a fully successful return (no exception raised) the manifest is
    truncated: a full restore (``to`` is None) empties the log entirely;
    a partial restore rewrites the log keeping only entries with
    ``ts <= to``. Snapshots no longer referenced by any surviving entry
    are pruned in both cases.
    """
    entries = list(reversed(manifest.iter_entries()))
    if to is not None:
        entries = [e for e in entries if e.ts > to]

    from trinity.atomic import atomic_write_bytes

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
                    f"manifest entry references missing snapshot "
                    f"{entry.prev_bytes_path!r} for {entry.path!r}"
                )
        elif entry.op == "delete":
            if target.exists():
                target.unlink()
                count += 1

    # Truncation: only run after a fully successful restore (no raise above).
    if to is None:
        # Full restore: drop the whole log and every snapshot.
        _truncate_log(manifest, [])
        _prune_snapshots(manifest, [], snapshots_dir=snapshots_dir)
    else:
        # Partial restore: keep entries with ts <= to.
        kept = [e for e in manifest.iter_entries() if e.ts <= to]
        _truncate_log(manifest, kept)
        _prune_snapshots(manifest, kept, snapshots_dir=snapshots_dir)
    return count


def _truncate_log(manifest: Manifest, kept: list[ManifestEntry]) -> None:
    """Rewrite the manifest log to contain exactly ``kept`` (oldest-first).

    An empty ``kept`` list empties the log. Uses the atomic-write helper
    so a crash mid-rewrite cannot corrupt the log.
    """
    from trinity.atomic import atomic_write_bytes

    manifest.path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"".join(entry.to_json().encode("utf-8") + b"\n" for entry in kept)
    atomic_write_bytes(manifest.path, payload)


def _referenced_snapshots(entries: list[ManifestEntry]) -> set[str]:
    """Return the set of snapshot paths referenced by ``entries``."""
    return {e.prev_bytes_path for e in entries if e.prev_bytes_path is not None}


def _prune_snapshots(
    manifest: Manifest,
    kept: list[ManifestEntry],
    *,
    snapshots_dir: Path | None = None,
) -> list[Path]:
    """Delete snapshot files under ``manifest_snapshots/`` that are not
    referenced by any entry in ``kept``.

    Never deletes a referenced snapshot. Returns the list of deleted
    snapshot paths (for observability/testing). Snapshots deduplicate
    by SHA (existing behaviour) so a snapshot referenced by two entries
    survives until neither references it.
    """
    sdir = snapshots_dir or paths.state_dir() / "manifest_snapshots"
    if not sdir.is_dir():
        return []
    referenced = _referenced_snapshots(kept)
    deleted: list[Path] = []
    for snap in sdir.iterdir():
        if snap.is_file() and str(snap) not in referenced:
            try:
                snap.unlink()
                deleted.append(snap)
            except OSError:
                pass
    return deleted


def truncate(manifest: Manifest) -> None:
    """Empty the manifest log and prune all snapshots.

    Kept for backwards compatibility; ``restore`` now calls the internal
    truncation helpers directly, but external callers (and tests) may
    still use this to reset state.
    """
    _truncate_log(manifest, [])
    _prune_snapshots(manifest, [])


def compact(manifest: Manifest, *, snapshots_dir: Path | None = None) -> int:
    """Drop oldest entries beyond ``_RETENTION_ENTRIES`` and prune their
    now-unreferenced snapshots.

    Returns the number of entries dropped. Called after a successful
    ``apply`` so the manifest and snapshot dir cannot grow unbounded
    under the daily systemd timer.
    """
    entries = manifest.iter_entries()
    if len(entries) <= _RETENTION_ENTRIES:
        return 0
    kept = entries[-_RETENTION_ENTRIES:]
    dropped = len(entries) - len(kept)
    _truncate_log(manifest, kept)
    _prune_snapshots(manifest, kept, snapshots_dir=snapshots_dir)
    return dropped
