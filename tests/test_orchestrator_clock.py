"""Tests for orchestrator clock position wiring."""

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
from trinity.theme.qmllint import QmlLintResult


def _make_config(tmp_path: Path, clock_enabled: bool = True) -> Config:
    from trinity.schema import ClockPosition, ThemeTokens
    return Config(
        surface=Surface(
            source=Source(provider="solid", options=SourceOptions()),
            fonts=Fonts(),
            login=Login(),
            lock=Lock(),
            theme_tokens=ThemeTokens(
                enabled=True,
                clock_position=ClockPosition(
                    enabled=clock_enabled,
                    alignment="top_left",
                )
            ),
            behaviour=Behaviour(
                shared_dir=str(tmp_path / "shared"),
                user_dir=str(tmp_path / "user"),
            ),
        )
    )


def test_orchestrator_clock_position_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drives apply_to_surfaces end-to-end with clock_position.enabled=true
    against a fixture Login.qml.
    """
    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setattr(paths, "templates_dir", lambda: templates)

    from trinity.backends import sddm_fork
    sddm_fork.VENDOR_BREEZE_DIR.mkdir(parents=True, exist_ok=True)

    login_qml_content = """\
import QtQuick
import QtQuick.Layouts

Item {
    id: root
    ColumnLayout {
        Text {
            id: clock
            text: "12:00"
        }
    }
}
"""
    vpath = sddm_fork.VENDOR_BREEZE_DIR / "Login.qml"
    vpath.write_text(login_qml_content, encoding="utf-8")
    (templates / "sddm_login.qml").write_text(login_qml_content, encoding="utf-8")

    targets = [
        ("sddm_login", vpath),
    ]
    monkeypatch.setattr(extract, "DEFAULT_TARGETS", targets)

    # Stub provider fetch and verify image
    from trinity.providers import FetchedImage
    fake_img = FetchedImage(
        data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
        content_type="image/png",
        suggested_extension=".png",
    )

    # Mock qmllint so it always passes
    fake_lint = QmlLintResult(ok=True, stdout="", stderr="", timed_out=False)

    cfg = _make_config(tmp_path, clock_enabled=True)

    with (
        patch("trinity.orchestrator.fetch_wallpaper", return_value=fake_img),
        patch("trinity.orchestrator.verify_image", return_value=fake_img.data),
        patch("trinity.theme.qmllint.lint_file", return_value=fake_lint),
    ):
        plan = apply_to_surfaces(cfg, manifest=Manifest(), dry_run=False)

    # Assert plan reports success
    assert any(
        "QML clock 'sddm_login': clock_position: applied" in line
        for line in plan
    )

    # Assert alignment landed in the fork theme directory
    fork_file = sddm_fork.FORK_THEME_DIR / "Login.qml"
    patched_content = fork_file.read_text(encoding="utf-8")
    assert "Layout.alignment: Qt.AlignTop | Qt.AlignLeft" in patched_content


def test_orchestrator_clock_position_qmllint_revert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qmllint-revert fires on a deliberately broken descriptor."""
    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setattr(paths, "templates_dir", lambda: templates)

    from trinity.backends import sddm_fork
    sddm_fork.VENDOR_BREEZE_DIR.mkdir(parents=True, exist_ok=True)

    login_qml_content = """\
import QtQuick
import QtQuick.Layouts

Item {
    id: root
    ColumnLayout {
        Text {
            id: clock
            text: "12:00"
        }
    }
}
"""
    vpath = sddm_fork.VENDOR_BREEZE_DIR / "Login.qml"
    vpath.write_text(login_qml_content, encoding="utf-8")
    (templates / "sddm_login.qml").write_text(login_qml_content, encoding="utf-8")

    targets = [
        ("sddm_login", vpath),
    ]
    monkeypatch.setattr(extract, "DEFAULT_TARGETS", targets)

    # Stub provider fetch and verify image
    from trinity.providers import FetchedImage
    fake_img = FetchedImage(
        data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
        content_type="image/png",
        suggested_extension=".png",
    )

    # Mock qmllint to return fail so revert triggers
    fake_lint = QmlLintResult(
        ok=False, stdout="", stderr="invalid QML syntax", timed_out=False
    )

    cfg = _make_config(tmp_path, clock_enabled=True)

    with (
        patch("trinity.orchestrator.fetch_wallpaper", return_value=fake_img),
        patch("trinity.orchestrator.verify_image", return_value=fake_img.data),
        patch("trinity.theme.qmllint.lint_file", return_value=fake_lint),
    ):
        plan = apply_to_surfaces(cfg, manifest=Manifest(), dry_run=False)

    # Assert plan contains the revert message
    assert any("LINT FAILED; reverted to pristine" in line for line in plan)

    # Assert the file is restored to pristine (login_qml_content)
    fork_file = sddm_fork.FORK_THEME_DIR / "Login.qml"
    restored_content = fork_file.read_text(encoding="utf-8")
    assert restored_content == login_qml_content
