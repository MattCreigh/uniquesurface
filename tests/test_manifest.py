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
