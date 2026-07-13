"""Tests for the orchestrator's QML drift handling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trinity import paths
from trinity.manifest import Manifest
from trinity.orchestrator import apply_to_surfaces
from trinity.schema import (
    Behaviour,
    Config,
    Fonts,
    Lock,
    Login,
    Source,
    SourceOptions,
    Surface,
)
from trinity.theme import extract


def _make_config(tmp_path: Path) -> Config:
    return Config(
        surface=Surface(
            source=Source(provider="bing", options=SourceOptions()),
            fonts=Fonts(),
            login=Login(),
            lock=Lock(),
            behaviour=Behaviour(
                shared_dir=str(tmp_path / "shared"),
                user_dir=str(tmp_path / "user"),
            ),
        )
    )


def test_drifted_qml_file_is_skipped_while_others_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When handle_drift raises DriftError for one file, the orchestrator
    reports the drift and a hint but still processes the other files."""
    # Seed pristine templates for all three DEFAULT_TARGETS so the
    # patcher has a baseline. Point the templates dir at tmp.
    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setattr(paths, "templates_dir", lambda: templates)

    # Create vendor files on disk (in tmp) for each DEFAULT_TARGETS entry.
    from trinity.backends import sddm_fork

    sddm_fork.VENDOR_BREEZE_DIR.mkdir(parents=True, exist_ok=True)
    targets = [
        ("sddm_login", sddm_fork.VENDOR_BREEZE_DIR / "Login.qml"),
        ("plasma_lockscreen_mainblock", tmp_path / "MainBlock.qml"),
        ("plasma_lockscreen_ui", tmp_path / "LockScreenUi.qml"),
    ]
    for name, vpath in targets:
        vpath.write_text("import QtQuick\nItem {}\n", encoding="utf-8")
        (templates / f"{name}.qml").write_text(
            "import QtQuick\nItem {}\n", encoding="utf-8"
        )
    monkeypatch.setattr(extract, "DEFAULT_TARGETS", targets)

    # Stub the provider fetch so we don't hit the network.
    from trinity.providers import FetchedImage

    fake_img = FetchedImage(
        data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
        content_type="image/png",
        suggested_extension=".png",
    )

    # Make handle_drift raise DriftError for the FIRST file only.
    from trinity.theme import drift

    real_handle_drift = drift.handle_drift
    call_count = {"n": 0}

    def flaky_handle_drift(name: str, vendor_path: Path) -> Path | None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First file: raise DriftError (simulating drift).
            raise drift.DriftError(
                name=name,
                vendor_path=vendor_path,
                backup_path=vendor_path.parent / f"{vendor_path.name}.drift.bak",
                pristine_sha="abc",
                on_disk_sha="def",
            )
        # Other files: no drift.
        return real_handle_drift(name, vendor_path)

    # Pillow needs a real image; stub verify_image to return the bytes.
    with (
        patch("trinity.orchestrator.fetch_wallpaper", return_value=fake_img),
        patch("trinity.orchestrator.verify_image", return_value=fake_img.data),
        patch("trinity.theme.drift.handle_drift", side_effect=flaky_handle_drift),
    ):
        m = Manifest(tmp_path / "manifest.jsonl")
        plan = apply_to_surfaces(
            _make_config(tmp_path),
            manifest=m,
            backends=[],  # skip desktop/lock/login backends
        )

    # The drifted file is reported with DRIFTED + hint.
    drift_lines = [line for line in plan if "DRIFTED" in line]
    assert len(drift_lines) == 1
    assert "sddm_login" in drift_lines[0]
    assert any("qml-update-templates" in line for line in plan)
    # The other two files were still processed (not drifted).
    applied = [line for line in plan if "applied" in line and "QML" in line]
    # They skip (no managed properties) but are still attempted, not aborted.
    assert len(applied) >= 2


def test_adopt_drift_adopts_and_patches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With adopt_drift=True, a DriftError is handled by adopting the
    drifted content as the new pristine and proceeding to patch."""
    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setattr(paths, "templates_dir", lambda: templates)

    from trinity.backends import sddm_fork

    sddm_fork.VENDOR_BREEZE_DIR.mkdir(parents=True, exist_ok=True)
    targets = [
        ("sddm_login", sddm_fork.VENDOR_BREEZE_DIR / "Login.qml"),
        ("plasma_lockscreen_mainblock", tmp_path / "MainBlock.qml"),
        ("plasma_lockscreen_ui", tmp_path / "LockScreenUi.qml"),
    ]
    for name, vpath in targets:
        vpath.write_text("import QtQuick\nItem {}\n", encoding="utf-8")
        (templates / f"{name}.qml").write_text(
            "import QtQuick\nItem {}\n", encoding="utf-8"
        )
    monkeypatch.setattr(extract, "DEFAULT_TARGETS", targets)

    from trinity.providers import FetchedImage
    from trinity.theme import drift

    fake_img = FetchedImage(
        data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
        content_type="image/png",
        suggested_extension=".png",
    )
    real_handle = drift.handle_drift
    call_count = {"n": 0}

    def flaky(name: str, vendor_path: Path) -> Path | None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise drift.DriftError(
                name=name,
                vendor_path=vendor_path,
                backup_path=vendor_path.parent / f"{vendor_path.name}.drift.bak",
                pristine_sha="abc",
                on_disk_sha="def",
            )
        return real_handle(name, vendor_path)

    with (
        patch("trinity.orchestrator.fetch_wallpaper", return_value=fake_img),
        patch("trinity.orchestrator.verify_image", return_value=fake_img.data),
        patch("trinity.theme.drift.handle_drift", side_effect=flaky),
    ):
        m = Manifest(tmp_path / "manifest.jsonl")
        plan = apply_to_surfaces(
            _make_config(tmp_path),
            manifest=m,
            backends=[],
            adopt_drift=True,
        )

    # The first file was adopted, not skipped.
    assert any("DRIFT ADOPTED" in line for line in plan)
    assert not any("DRIFTED:" in line for line in plan)
