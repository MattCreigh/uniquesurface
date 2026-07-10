"""Tests for the bundled-font installer and fontconfig lookup."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trinity.theme import font_install


def _fake_fc_match(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=(), returncode=0, stdout=stdout, stderr="")


def test_is_installed_false_when_fc_match_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(font_install.shutil, "which", lambda _n: None)
    assert font_install.is_installed("Inter") is False


def test_is_installed_exact_family_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(font_install.shutil, "which", lambda _n: "/usr/bin/fc-match")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _fake_fc_match("Inter,DejaVu Sans")
    )
    assert font_install.is_installed("Inter") is True


def test_is_installed_rejects_fallback_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fc-match always resolves *something*; a fallback to a different
    family must not count as installed."""
    monkeypatch.setattr(font_install.shutil, "which", lambda _n: "/usr/bin/fc-match")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _fake_fc_match("DejaVu Sans")
    )
    assert font_install.is_installed("Inter") is False


def test_install_copies_source_to_user_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force_user=True installs into ~/.local/share/fonts (HOME is
    redirected to tmp by the autouse fixture)."""
    src = tmp_path / "MyFont.ttf"
    src.write_bytes(b"\x00\x01\x00\x00fake-ttf")
    monkeypatch.setattr(font_install, "_run_fc_cache", lambda _t: False)
    result = font_install.install(source=src, force_user=True)
    assert result.system_wide is False
    assert result.installed_to.exists()
    assert result.installed_to.read_bytes() == src.read_bytes()
    assert ".local/share/fonts" in str(result.installed_to)


def test_install_raises_when_no_font_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(font_install, "_bundled_font", lambda: None)
    with pytest.raises(FileNotFoundError, match="No bundled Inter font"):
        font_install.install()


def test_bundled_font_is_shipped() -> None:
    """The wheel must actually contain the Inter TTF the installer needs."""
    found = font_install._bundled_font()
    assert found is not None
    assert found.name == "Inter-Regular.ttf"
    assert found.is_file()
