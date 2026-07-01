"""End-to-end-ish tests that drive the CLI and the orchestrator together."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner

from usurface.cli import main
from usurface.providers.builtin import bing
from usurface.theme import extract


def test_config_init_writes_starter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(main, ["config", "init"])
    assert result.exit_code == 0, result.output
    config_path = Path(os_environ("XDG_CONFIG_HOME")) / "usurface" / "config.toml"
    assert config_path.exists()
    text = config_path.read_text()
    assert 'provider = "bing"' in text


def test_config_validate_against_starter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runner = CliRunner()
    runner.invoke(main, ["config", "init"])
    result = runner.invoke(main, ["config", "validate"])
    assert result.exit_code == 0


def test_apply_dry_run_with_solid_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set up an isolated XDG env.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
    monkeypatch.setenv("USURFACE_SHARED_DIR", str(tmp_path / "shared"))
    cfg_dir = tmp_path / "xdg_config" / "usurface"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(
        """\
[surface]
schema_version = 1

[surface.source]
provider = "solid"

[surface.source.options]
color = "#1d99f3"
width = 32
height = 18

[surface.behaviour]
shared_dir = "%s"
user_dir = "%s"
"""
        % (str(tmp_path / "shared"), str(tmp_path / "user_state"))
    )

    runner = CliRunner()
    result = runner.invoke(main, ["apply", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "fetch from provider" in result.output
    assert "kwriteconfig6" in result.output


def test_apply_real_writes_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, respx_mock: respx.router.MockRouter
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
    monkeypatch.setenv("USURFACE_SHARED_DIR", str(tmp_path / "shared"))

    # Replace the SDDM theme.conf path with one we own.
    fake_sddm = tmp_path / "sddm" / "breeze" / "theme.conf"
    fake_sddm.parent.mkdir(parents=True)
    fake_sddm.write_text(
        "[General]\ntype=image\nbackground=/old.jpg\n", encoding="utf-8"
    )
    from usurface.backends import login as login_mod

    monkeypatch.setattr(login_mod, "_THEME_CONF_PATH", fake_sddm)

    # Mock extract targets and pristine template directory
    from usurface import paths
    from usurface.theme import extract as extract_mod

    fake_login_qml = tmp_path / "Login.qml"
    fake_login_qml.write_text(
        'import QtQuick\nItem { property string fontFamily: "Lato" }\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(paths, "templates_dir", lambda: tmp_path / "templates")
    from usurface.theme.extract import copy_pristine_bytes

    copy_pristine_bytes(
        "sddm_login", b'import QtQuick\nItem { property string fontFamily: "Lato" }\n'
    )

    monkeypatch.setattr(
        extract_mod,
        "DEFAULT_TARGETS",
        [
            ("sddm_login", fake_login_qml),
        ],
    )

    # Mock Bing. Use a real 1x1 JPEG so Pillow can decode it.
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "#1d99f3").save(buf, format="JPEG", quality=70)
    image_bytes = buf.getvalue()
    respx_mock.get(bing._METADATA_URL).mock(
        return_value=httpx.Response(
            200,
            json={"images": [{"url": "/th?id=OHR.Test_1920x1080.jpg"}]},
        )
    )
    respx_mock.get("https://www.bing.com/th?id=OHR.Test_1920x1080.jpg").mock(
        return_value=httpx.Response(
            200, content=image_bytes, headers={"content-type": "image/jpeg"}
        )
    )

    cfg_dir = tmp_path / "xdg_config" / "usurface"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(
        """\
[surface]
schema_version = 1

[surface.source]
provider = "bing"

[surface.source.options]
mkt = "en-US"

[surface.behaviour]
shared_dir = "%s"
user_dir = "%s"
"""
        % (str(tmp_path / "shared"), str(tmp_path / "user_state"))
    )

    runner = CliRunner()
    result = runner.invoke(main, ["apply"])
    assert result.exit_code == 0, result.output

    user_state = tmp_path / "user_state"
    shared = tmp_path / "shared"
    assert (user_state / "last_wallpaper.jpg").exists()
    assert (shared / "last_wallpaper.jpg").exists()
    # SDDM theme.conf got updated to point at the shared wallpaper.
    assert "last_wallpaper.jpg" in fake_sddm.read_text()

    # The manifest has entries (wallpapers, theme.conf, and QML screens)
    from usurface.manifest import Manifest

    m = Manifest(tmp_path / "xdg_state" / "usurface" / "manifest.jsonl")
    entries = m.iter_entries()
    assert len(entries) > 0
    # At least wallpaper writes, login config write, and QML write should be tracked.
    paths_tracked = [e.path for e in entries]
    assert any(str(user_state / "last_wallpaper.jpg") in p for p in paths_tracked)
    assert any(str(shared / "last_wallpaper.jpg") in p for p in paths_tracked)
    assert any(str(fake_sddm) in p for p in paths_tracked)
    assert any(str(fake_login_qml) in p for p in paths_tracked)

    # SDDM Login.qml is patched with Inter font
    qml_content = fake_login_qml.read_text(encoding="utf-8")
    assert "Inter" in qml_content
    assert "/* @usurface:start */" in qml_content


def test_status_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 0
    assert "manifest entries:" in result.output


def test_provider_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["provider", "list"])
    assert result.exit_code == 0
    assert "bing" in result.output
    assert "solid" in result.output
    assert "file" in result.output


def test_doctor_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])
    # exit code 0 or 1 depending on warnings, but command must not crash.
    assert result.exit_code in (0, 1)


def test_qml_update_templates_writes_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mock a fake vendor path so extract has something to read.
    fake_sddm = tmp_path / "fake_sddm" / "Login.qml"
    fake_sddm.parent.mkdir(parents=True)
    fake_sddm.write_text("import QtQuick\nItem {}\n", encoding="utf-8")
    fake_plasma_dir = tmp_path / "fake_plasma" / "lockscreen"
    fake_plasma_dir.mkdir(parents=True)
    (fake_plasma_dir / "MainBlock.qml").write_text(
        "import QtQuick\nItem {}\n", encoding="utf-8"
    )
    (fake_plasma_dir / "LockScreenUi.qml").write_text(
        "import QtQuick\nItem {}\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        extract,
        "DEFAULT_TARGETS",
        [
            ("sddm_login", fake_sddm),
            ("plasma_lockscreen_mainblock", fake_plasma_dir / "MainBlock.qml"),
            ("plasma_lockscreen_ui", fake_plasma_dir / "LockScreenUi.qml"),
        ],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["qml-update-templates", "--yes"])
    assert result.exit_code == 0, result.output


def os_environ(name: str) -> str:
    import os

    return os.environ[name]
