"""Tests for the manifest store."""

from __future__ import annotations

from pathlib import Path


from usurface import manifest


def test_append_creates_file_and_records_entry(tmp_path: Path) -> None:
    log = tmp_path / "manifest.jsonl"
    m = manifest.Manifest(log)
    entry = m.append(
        op="write",
        path="/etc/example.conf",
        prev_sha256=None,
        new_sha256="deadbeef",
    )
    assert log.exists()
    assert entry.op == "write"
    assert entry.path == "/etc/example.conf"
    assert entry.prev_sha256 is None
    assert entry.new_sha256 == "deadbeef"

    entries = m.iter_entries()
    assert len(entries) == 1
    assert entries[0] == entry


def test_iter_returns_oldest_first(tmp_path: Path) -> None:
    log = tmp_path / "manifest.jsonl"
    m = manifest.Manifest(log)
    m.append(op="write", path="/a", prev_sha256=None, new_sha256="1")
    m.append(op="write", path="/b", prev_sha256=None, new_sha256="2")
    m.append(op="write", path="/c", prev_sha256=None, new_sha256="3")

    entries = m.iter_entries()
    assert [e.path for e in entries] == ["/a", "/b", "/c"]


def test_head_returns_last_n(tmp_path: Path) -> None:
    log = tmp_path / "manifest.jsonl"
    m = manifest.Manifest(log)
    for i in range(5):
        m.append(op="write", path=f"/p{i}", prev_sha256=None, new_sha256=str(i))
    last_two = m.head(2)
    assert [e.path for e in last_two] == ["/p3", "/p4"]


def test_write_tracked_records_sha_before_and_after(tmp_path: Path) -> None:
    log = tmp_path / "manifest.jsonl"
    snaps = tmp_path / "snaps"
    m = manifest.Manifest(log)

    target = tmp_path / "thing.bin"
    target.write_bytes(b"original")
    entry = manifest.write_tracked(m, target, b"new", snapshots_dir=snaps)
    assert target.read_bytes() == b"new"
    assert entry.prev_sha256 == manifest.sha256_bytes(b"original")
    assert entry.new_sha256 == manifest.sha256_bytes(b"new")
    assert entry.prev_bytes_path
    assert Path(entry.prev_bytes_path).read_bytes() == b"original"


def test_write_tracked_when_target_missing(tmp_path: Path) -> None:
    log = tmp_path / "manifest.jsonl"
    m = manifest.Manifest(log)
    target = tmp_path / "new.bin"
    entry = manifest.write_tracked(
        m, target, b"hello", snapshots_dir=tmp_path / "snaps"
    )
    assert entry.prev_sha256 is None
    # snapshot_previous_bytes returns (None, None) — NOT ("", "") — when the
    # target doesn't exist. The empty-string was a type lie that broke the
    # restore() "prev_sha256 is None" fallthrough branch.
    assert entry.prev_bytes_path is None
    assert target.read_bytes() == b"hello"


def test_snapshot_previous_bytes_returns_none_none_when_missing(
    tmp_path: Path,
) -> None:
    """The helper's signature is tuple[str | None, str | None]; a missing
    target must yield (None, None), not (None, '')."""
    log = tmp_path / "manifest.jsonl"
    m = manifest.Manifest(log)
    prev_sha, prev_snap = manifest.snapshot_previous_bytes(
        m, tmp_path / "does_not_exist", snapshots_dir=tmp_path / "snaps"
    )
    assert prev_sha is None
    assert prev_snap is None


def test_restore_unwrites_to_previous_bytes(tmp_path: Path) -> None:
    log = tmp_path / "manifest.jsonl"
    snaps = tmp_path / "snaps"
    m = manifest.Manifest(log)

    target = tmp_path / "thing.bin"
    target.write_bytes(b"ORIGINAL")
    manifest.write_tracked(m, target, b"FIRST", snapshots_dir=snaps)
    manifest.write_tracked(m, target, b"SECOND", snapshots_dir=snaps)
    manifest.write_tracked(m, target, b"THIRD", snapshots_dir=snaps)

    assert target.read_bytes() == b"THIRD"
    count = manifest.restore(m)
    # Three "write" entries, each with a previous snapshot: all three should
    # be reverted to the state immediately preceding each write.
    assert count == 3
    assert target.read_bytes() == b"ORIGINAL"


def test_restore_handles_entry_where_target_did_not_exist(tmp_path: Path) -> None:
    log = tmp_path / "manifest.jsonl"
    m = manifest.Manifest(log)
    target = tmp_path / "created-later.bin"
    manifest.write_tracked(m, target, b"fresh", snapshots_dir=tmp_path / "snaps")
    assert target.exists()

    count = manifest.restore(m)
    assert count == 1
    assert not target.exists()


def test_restore_to_timestamp_stops_earlier(tmp_path: Path) -> None:
    log = tmp_path / "manifest.jsonl"
    snaps = tmp_path / "snaps"
    m = manifest.Manifest(log)

    target = tmp_path / "thing.bin"
    target.write_bytes(b"v0")
    first = manifest.write_tracked(m, target, b"v1", snapshots_dir=snaps)
    manifest.write_tracked(m, target, b"v2", snapshots_dir=snaps)
    manifest.write_tracked(m, target, b"v3", snapshots_dir=snaps)

    count = manifest.restore(m, to=first.ts)
    assert count == 2
    assert target.read_bytes() == b"v1"


# --- item 4: restore lifecycle + compaction ---


def test_full_restore_empties_log_and_prunes_snapshots(tmp_path: Path) -> None:
    """A full restore truncates the whole log and deletes every snapshot."""
    log = tmp_path / "manifest.jsonl"
    snaps = tmp_path / "snaps"
    m = manifest.Manifest(log)
    target = tmp_path / "thing.bin"
    target.write_bytes(b"orig")
    manifest.write_tracked(m, target, b"v1", snapshots_dir=snaps)
    manifest.write_tracked(m, target, b"v2", snapshots_dir=snaps)
    assert snaps.is_dir() and any(snaps.iterdir())
    count = manifest.restore(m, snapshots_dir=snaps)
    assert count == 2
    assert target.read_bytes() == b"orig"
    # Log is empty.
    assert m.iter_entries() == []
    # Snapshots all pruned.
    assert not any(snaps.iterdir())


def test_partial_restore_keeps_older_entries_and_snapshots(tmp_path: Path) -> None:
    """A partial restore (--to) keeps entries with ts <= to and their
    snapshots, pruning only the snapshots of the reverted entries."""
    log = tmp_path / "manifest.jsonl"
    snaps = tmp_path / "snaps"
    m = manifest.Manifest(log)
    target = tmp_path / "thing.bin"
    target.write_bytes(b"v0")
    first = manifest.write_tracked(m, target, b"v1", snapshots_dir=snaps)
    manifest.write_tracked(m, target, b"v2", snapshots_dir=snaps)
    manifest.write_tracked(m, target, b"v3", snapshots_dir=snaps)
    count = manifest.restore(m, to=first.ts, snapshots_dir=snaps)
    assert count == 2
    assert target.read_bytes() == b"v1"
    # The first entry (ts <= first.ts) is kept.
    kept = m.iter_entries()
    assert len(kept) == 1
    assert kept[0].ts == first.ts
    # Its referenced snapshot survives; the others are pruned.
    remaining = {p.name for p in snaps.iterdir()}
    assert kept[0].prev_bytes_path is not None
    assert Path(kept[0].prev_bytes_path).name in remaining


def test_dedup_snapshot_survives_until_neither_references_it(tmp_path: Path) -> None:
    """Snapshots dedup by SHA. A snapshot referenced by two entries must
    survive until neither remaining entry references it."""
    log = tmp_path / "manifest.jsonl"
    snaps = tmp_path / "snaps"
    m = manifest.Manifest(log)
    target = tmp_path / "thing.bin"
    target.write_bytes(b"SHARED")  # prev content for both writes below
    # Two writes with the SAME previous content -> same snapshot SHA.
    manifest.write_tracked(m, target, b"v1", snapshots_dir=snaps)
    target.write_bytes(b"SHARED")  # reset to shared content
    second = manifest.write_tracked(m, target, b"v2", snapshots_dir=snaps)
    # Both entries reference the same snapshot file (dedup by SHA).
    first_entry = m.iter_entries()[0]
    assert first_entry.prev_bytes_path == second.prev_bytes_path
    snap_path = second.prev_bytes_path
    assert snap_path is not None
    # Partial restore keeping only the first entry: snapshot still
    # referenced by the kept entry -> must survive.
    manifest.restore(m, to=first_entry.ts, snapshots_dir=snaps)
    assert Path(snap_path).exists()


def test_compaction_keeps_newest_n_entries(tmp_path: Path) -> None:
    """compact() drops oldest entries beyond _RETENTION_ENTRIES and
    prunes their snapshots, keeping exactly the newest N."""
    log = tmp_path / "manifest.jsonl"
    snaps = tmp_path / "snaps"
    m = manifest.Manifest(log)
    # Create more entries than the retention threshold by writing many
    # distinct versions to a single target.
    target = tmp_path / "thing.bin"
    target.write_bytes(b"seed")
    for i in range(manifest._RETENTION_ENTRIES + 5):
        manifest.write_tracked(m, target, f"v{i}".encode(), snapshots_dir=snaps)
    total = len(m.iter_entries())
    assert total == manifest._RETENTION_ENTRIES + 5
    dropped = manifest.compact(m, snapshots_dir=snaps)
    assert dropped == 5  # the oldest 5 versions beyond the threshold
    kept = m.iter_entries()
    assert len(kept) == manifest._RETENTION_ENTRIES
    # Every remaining snapshot reference still exists.
    for e in kept:
        if e.prev_bytes_path is not None:
            assert Path(e.prev_bytes_path).exists()
