"""Tests for the SDDM theme fork (Phase 5).

The fork copies the vendor Breeze theme to
``/usr/share/sddm/themes/trinity-breeze/``, patches the fork's
``Login.qml`` with the font/theme tokens, and writes a drop-in at
``/etc/sddm.conf.d/trinity.conf`` selecting it via ``[Theme]
Current=trinity-breeze``.  ``restore`` reverts the drop-in and
removes the fork — the vendor Breeze theme is untouched.

These tests use ``tmp_path`` for both the source breeze theme and the
fork destination so no real filesystem path is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trinity.backends import sddm_fork
from trinity.manifest import Manifest


@pytest.fixture
def fake_breeze_theme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a fake breeze theme dir under tmp_path and patch the
    fork module to use it as the source.

    Returns the source dir.
    """
    src = tmp_path / "breeze"
    src.mkdir()
    (src / "theme.conf").write_text(
        "[General]\ntype=image\nbackground=/old.jpg\n", encoding="utf-8"
    )
    (src / "Login.qml").write_text(
        'import QtQuick\nItem { property string fontFamily: "Lato" }\n',
        encoding="utf-8",
    )
    (src / "metadata.desktop").write_text(
        "[SddmGreeterTheme]\nName=Breeze\nMainScript=Main.qml\n",
        encoding="utf-8",
    )
    # A subdir to test recursive copy.
    (src / "components").mkdir()
    (src / "components" / "Button.qml").write_text(
        "import QtQuick\nItem {}\n", encoding="utf-8"
    )
    monkeypatch.setattr(sddm_fork, "VENDOR_BREEZE_DIR", src)
    return src


@pytest.fixture
def fake_fork_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Patch the fork module to use a tmp fork dir + tmp dropin path.

    Returns ``(fork_dir, dropin_path)``.
    """
    fork = tmp_path / "themes" / "trinity-breeze"
    dropin = tmp_path / "sddm.conf.d" / "trinity.conf"
    monkeypatch.setattr(sddm_fork, "FORK_THEME_DIR", fork)
    monkeypatch.setattr(sddm_fork, "DROPIN_PATH", dropin)
    return fork, dropin


def test_fork_copies_all_files(
    fake_breeze_theme: Path,
    fake_fork_dest: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """fork_breeze_theme() recursively copies the source theme."""
    fork, _ = fake_fork_dest
    manifest = Manifest(tmp_path / "manifest.jsonl")
    result = sddm_fork.fork_breeze_theme(manifest)
    assert result.created
    assert fork.is_dir()
    assert (fork / "theme.conf").exists()
    assert (fork / "Login.qml").exists()
    # Subdirs are copied.
    assert (fork / "components" / "Button.qml").exists()


def test_fork_patches_metadata_name(
    fake_breeze_theme: Path,
    fake_fork_dest: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """The fork's metadata.desktop has Name=Trinity Breeze so it's
    distinguishable in the SDDM theme picker."""
    fork, _ = fake_fork_dest
    manifest = Manifest(tmp_path / "manifest.jsonl")
    sddm_fork.fork_breeze_theme(manifest)
    text = (fork / "metadata.desktop").read_text(encoding="utf-8")
    assert "Trinity Breeze" in text
    # The original "Name=Breeze" line is gone (replaced, not appended).
    assert "Name=Breeze\n" not in text


def test_fork_records_each_file_in_manifest(
    fake_breeze_theme: Path,
    fake_fork_dest: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """Every file written by the fork is recorded in the manifest."""
    fork, _ = fake_fork_dest
    manifest_path = tmp_path / "manifest.jsonl"
    manifest = Manifest(manifest_path)
    sddm_fork.fork_breeze_theme(manifest)
    entries = list(manifest.iter_entries())
    paths = {e.path for e in entries}
    assert str(fork / "theme.conf") in paths
    assert str(fork / "Login.qml") in paths
    assert str(fork / "components" / "Button.qml") in paths


def test_fork_returns_false_when_source_missing(
    fake_fork_dest: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the breeze theme source doesn't exist, the fork is skipped."""
    monkeypatch.setattr(sddm_fork, "VENDOR_BREEZE_DIR", tmp_path / "nonexistent")
    manifest = Manifest(tmp_path / "manifest.jsonl")
    result = sddm_fork.fork_breeze_theme(manifest)
    assert not result.created
    assert "not found" in result.message


def test_fork_refreshes_existing(
    fake_breeze_theme: Path,
    fake_fork_dest: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """A second fork replaces the existing fork dir cleanly."""
    fork, _ = fake_fork_dest
    manifest = Manifest(tmp_path / "manifest.jsonl")
    sddm_fork.fork_breeze_theme(manifest)
    # Stale file from a previous run.
    stale = fork / "stale.txt"
    stale.write_text("stale", encoding="utf-8")
    # Re-fork.
    sddm_fork.fork_breeze_theme(manifest)
    assert not stale.exists()


def test_write_dropin_writes_conf_file(
    fake_fork_dest: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """write_dropin() writes the [Theme] Current=trinity-breeze conf."""
    _, dropin = fake_fork_dest
    manifest = Manifest(tmp_path / "manifest.jsonl")
    written = sddm_fork.write_dropin(manifest)
    assert written == dropin
    text = dropin.read_text(encoding="utf-8")
    assert "Current=trinity-breeze" in text


def test_is_active_returns_false_when_nothing_exists(
    fake_fork_dest: tuple[Path, Path],
) -> None:
    """is_active() returns False when neither the fork nor the dropin
    exists."""
    assert sddm_fork.is_active() is False


def test_is_active_returns_false_when_fork_but_no_dropin(
    fake_breeze_theme: Path,
    fake_fork_dest: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """is_active() returns False when the fork exists but the dropin
    doesn't."""
    _fork, _dropin = fake_fork_dest
    manifest = Manifest(tmp_path / "manifest.jsonl")
    sddm_fork.fork_breeze_theme(manifest)
    assert sddm_fork.is_active() is False


def test_is_active_returns_true_when_both_present(
    fake_breeze_theme: Path,
    fake_fork_dest: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """is_active() returns True when the fork + dropin are both
    present and the dropin selects trinity-breeze."""
    manifest = Manifest(tmp_path / "manifest.jsonl")
    sddm_fork.fork_breeze_theme(manifest)
    sddm_fork.write_dropin(manifest)
    assert sddm_fork.is_active() is True


def test_remove_dropin_removes_file(
    fake_fork_dest: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """remove_dropin() deletes the conf file if it exists."""
    _, dropin = fake_fork_dest
    manifest = Manifest(tmp_path / "manifest.jsonl")
    sddm_fork.write_dropin(manifest)
    assert dropin.exists()
    removed = sddm_fork.remove_dropin(manifest)
    assert removed
    assert not dropin.exists()


def test_remove_dropin_returns_false_when_absent(
    fake_fork_dest: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """remove_dropin() returns False if the conf file doesn't exist."""
    manifest = Manifest(tmp_path / "manifest.jsonl")
    removed = sddm_fork.remove_dropin(manifest)
    assert not removed


def test_remove_fork_removes_directory(
    fake_breeze_theme: Path,
    fake_fork_dest: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """remove_fork() deletes the fork dir if it exists."""
    fork, _ = fake_fork_dest
    manifest = Manifest(tmp_path / "manifest.jsonl")
    sddm_fork.fork_breeze_theme(manifest)
    assert fork.is_dir()
    removed = sddm_fork.remove_fork(manifest)
    assert removed
    assert not fork.exists()


def test_remove_fork_returns_false_when_absent(
    fake_fork_dest: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """remove_fork() returns False if the fork dir doesn't exist."""
    manifest = Manifest(tmp_path / "manifest.jsonl")
    removed = sddm_fork.remove_fork(manifest)
    assert not removed


def test_patch_metadata_name_replaces_first_name_line() -> None:
    """_patch_metadata_name replaces the first ``Name=`` line."""
    text = "[SddmGreeterTheme]\nName=Breeze\nDescription=foo\n"
    out = sddm_fork._patch_metadata_name(text)
    assert "Trinity Breeze" in out
    # Other lines preserved.
    assert "Description=foo" in out
