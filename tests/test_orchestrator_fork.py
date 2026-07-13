"""Tests for the orchestrator's SDDM theme-fork wiring.

The fork step (theme_tokens enabled) must behave like the surface
backends: best-effort, idempotent, and skipped where SDDM isn't the
greeter. See ``trinity.backends.sddm_fork`` for the fork itself.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trinity import paths
from trinity.manifest import Manifest
from trinity.orchestrator import apply_to_surfaces
from trinity.providers import FetchedImage
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

_FAKE_IMG = FetchedImage(
    data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
    content_type="image/png",
    suggested_extension=".png",
)


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


@pytest.fixture
def qml_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed tmp vendor QML files + pristine templates and point the
    extract targets and the fork's vendor dir at them (hermetic)."""
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


def _apply(tmp_path: Path, manifest: Manifest, backends: list | None) -> list[str]:
    with (
        patch("trinity.orchestrator.fetch_wallpaper", return_value=_FAKE_IMG),
        patch("trinity.orchestrator.verify_image", return_value=_FAKE_IMG.data),
    ):
        return apply_to_surfaces(
            _make_config(tmp_path),
            manifest=manifest,
            backends=backends,
        )


def test_fork_failure_does_not_abort_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qml_targets: None
) -> None:
    """A fork failure (typically EPERM on a normal machine's user-mode
    timer run) is reported with a sudo hint but the surface backends
    still run — same best-effort doctrine as backend failures."""
    from trinity.backends import sddm_fork

    def boom(manifest: Manifest) -> None:
        raise PermissionError("[Errno 13] Permission denied: '/usr/share/sddm'")

    monkeypatch.setattr(sddm_fork, "fork_breeze_theme", boom)

    applied: list[Path] = []

    class FakeBackend:
        name = "desktop"

        def apply(self, manifest: Manifest, wallpaper: Path) -> None:
            applied.append(wallpaper)

        def dry_run_plan(self, wallpaper: Path) -> list[str]:
            return []

    plan = _apply(tmp_path, Manifest(tmp_path / "m.jsonl"), [FakeBackend()])

    assert any("sddm theme fork FAILED" in line for line in plan)
    assert any("sudo" in line for line in plan)
    assert applied, "surface backends must still run after a fork failure"


def test_fork_skipped_when_plasmalogin_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qml_targets: None
) -> None:
    """With plasmalogin as the greeter, the SDDM fork + drop-in would
    be inert; the orchestrator skips them entirely."""
    from trinity.backends import login as login_mod
    from trinity.backends import sddm_fork

    monkeypatch.setattr(login_mod, "is_plasmalogin_active", lambda: True)

    plan = _apply(tmp_path, Manifest(tmp_path / "m.jsonl"), [])

    assert any("plasmalogin" in line for line in plan if "fork" in line)
    assert not sddm_fork.FORK_THEME_DIR.exists()
    assert not sddm_fork.DROPIN_PATH.exists()


def test_second_apply_does_not_rebuild_fork(tmp_path: Path, qml_targets: None) -> None:
    """Repeated applies converge: the fork is copied once, then
    reported up to date; the drop-in is only rewritten if missing."""
    from trinity.backends import sddm_fork

    m = Manifest(tmp_path / "m.jsonl")

    first = _apply(tmp_path, m, [])
    assert any("forked" in line for line in first)
    assert any("wrote drop-in" in line for line in first)

    second = _apply(tmp_path, m, [])
    assert any("up to date" in line for line in second)
    assert not any("wrote drop-in" in line for line in second)

    # A deleted drop-in is restored on the next apply (self-healing).
    sddm_fork.DROPIN_PATH.unlink()
    third = _apply(tmp_path, m, [])
    assert any("wrote drop-in" in line for line in third)
