"""Tests for temporal cyclical provisioning (trinity cycle)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image as PILImage

from trinity.cli import main
from trinity.manifest import Manifest
from trinity.orchestrator import apply_to_surfaces
from trinity.providers import FetchedImage
from trinity.schema import (
    Behaviour,
    Config,
    Source,
    SourceOptions,
    Surface,
)

_FAKE_JPEG = b""
_buf = io.BytesIO()
PILImage.new("RGB", (8, 8), "#1d99f3").save(_buf, format="JPEG", quality=70)
_FAKE_JPEG = _buf.getvalue()


def _fake_fetched() -> FetchedImage:
    return FetchedImage(
        data=_FAKE_JPEG,
        content_type="image/jpeg",
        suggested_extension=".jpg",
    )


def _make_cycle_config(tmp_path: Path, *, provider: str = "bing") -> Config:
    return Config(
        surface=Surface(
            source=Source(provider=provider, options=SourceOptions()),
            behaviour=Behaviour(
                shared_dir=str(tmp_path / "shared"),
                user_dir=str(tmp_path / "user"),
            ),
        )
    )


# --- temporal_offset in refresh state ----------------------------------


def test_refresh_state_has_temporal_offset(tmp_path: Path) -> None:
    """RefreshState includes temporal_offset (default 0)."""
    from trinity.refresh_state import RefreshState

    state = RefreshState(
        fingerprint="abc",
        probe_token="tok",
        image_sha256="sha",
        wallpaper_path="/tmp/wall.jpg",
        applied_at="2026-01-01T00:00:00Z",
    )
    assert state.temporal_offset == 0


def test_refresh_state_temporal_offset_round_trip(tmp_path: Path) -> None:
    """temporal_offset is saved and loaded correctly."""
    from trinity import refresh_state

    state = refresh_state.RefreshState(
        fingerprint="abc",
        probe_token="tok",
        image_sha256="sha",
        wallpaper_path="/tmp/wall.jpg",
        applied_at="2026-01-01T00:00:00Z",
        temporal_offset=3,
    )
    path = tmp_path / "state.json"
    refresh_state.save(path, state)
    loaded = refresh_state.load(path)
    assert loaded is not None
    assert loaded.temporal_offset == 3


def test_cycle_token_combines_fingerprint_and_offset() -> None:
    """cycle_token combines the provider fingerprint + offset."""
    from trinity.refresh_state import cycle_token, source_fingerprint

    base = source_fingerprint("bing", {"mkt": "en-US"})
    token = cycle_token("bing", {"mkt": "en-US"}, 3)
    assert token == f"{base}:3"
    token2 = cycle_token("bing", {"mkt": "en-US"}, 5)
    assert token2 != token


# --- orchestrator with temporal_offset ---------------------------------


def test_apply_with_temporal_offset_injects_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply_to_surfaces with temporal_offset injects index into options."""
    cfg = _make_cycle_config(tmp_path)
    captured_options: dict = {}

    def fake_fetch(config, pm=None):
        captured_options.update(config.surface.source.options.model_dump())
        return _fake_fetched()

    monkeypatch.setattr("trinity.orchestrator.fetch_wallpaper", fake_fetch)
    apply_to_surfaces(cfg, manifest=Manifest(), dry_run=False, temporal_offset=3)
    assert captured_options.get("index") == 3


def test_apply_without_temporal_offset_uses_default_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply_to_surfaces without temporal_offset uses the config's index."""
    cfg = _make_cycle_config(tmp_path)
    captured_options: dict = {}

    def fake_fetch(config, pm=None):
        captured_options.update(config.surface.source.options.model_dump())
        return _fake_fetched()

    monkeypatch.setattr("trinity.orchestrator.fetch_wallpaper", fake_fetch)
    apply_to_surfaces(cfg, manifest=Manifest(), dry_run=False)
    assert captured_options.get("index", 0) == 0


# --- trinity cycle CLI --------------------------------------------------


def _write_config(tmp_path: Path) -> Path:
    cfg_dir = tmp_path / "xdg_config" / "trinity"
    cfg_dir.mkdir(parents=True)
    shared = str(tmp_path / "shared")
    user_state = str(tmp_path / "user")
    config_path = cfg_dir / "config.toml"
    config_path.write_text(
        f"""\
[surface]
schema_version = 1
[surface.source]
provider = "bing"
[surface.source.options]
mkt = "en-US"
[surface.behaviour]
shared_dir = "{shared}"
user_dir = "{user_state}"
[surface.theme_tokens]
enabled = false
"""
    )
    return config_path


def test_cycle_cli_with_offset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """trinity cycle --offset 3 passes the offset through."""
    config_path = _write_config(tmp_path)

    from trinity import paths

    monkeypatch.setattr(paths, "config_file", lambda: config_path)
    monkeypatch.setattr(paths, "manifest_file", lambda: tmp_path / "manifest.jsonl")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    monkeypatch.setattr(
        "trinity.orchestrator.fetch_wallpaper",
        lambda *_a, **_kw: _fake_fetched(),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["cycle", "--offset", "3"])
    assert result.exit_code == 0, result.output
    assert "offset 3" in result.output


def test_cycle_cli_no_offset_increments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """trinity cycle without --offset increments by 1."""
    config_path = _write_config(tmp_path)

    from trinity import paths

    monkeypatch.setattr(paths, "config_file", lambda: config_path)
    monkeypatch.setattr(paths, "manifest_file", lambda: tmp_path / "manifest.jsonl")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    monkeypatch.setattr(
        "trinity.orchestrator.fetch_wallpaper",
        lambda *_a, **_kw: _fake_fetched(),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["cycle"])
    assert result.exit_code == 0, result.output
    assert "offset 1" in result.output


def test_cycle_cli_does_not_mutate_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """trinity cycle does not mutate config.toml."""
    config_path = _write_config(tmp_path)
    config_content = config_path.read_text()

    from trinity import paths

    monkeypatch.setattr(paths, "config_file", lambda: config_path)
    monkeypatch.setattr(paths, "manifest_file", lambda: tmp_path / "manifest.jsonl")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    monkeypatch.setattr(
        "trinity.orchestrator.fetch_wallpaper",
        lambda *_a, **_kw: _fake_fetched(),
    )

    runner = CliRunner()
    runner.invoke(main, ["cycle", "--offset", "3"])
    assert config_path.read_text() == config_content


# --- cycle CLI error paths --------------------------------------------


def test_cycle_cli_no_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """trinity cycle without a config exits with a usage error."""
    from trinity import paths

    monkeypatch.setattr(paths, "config_file", lambda: tmp_path / "nonexistent.toml")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    runner = CliRunner()
    result = runner.invoke(main, ["cycle", "--offset", "3"])
    assert result.exit_code != 0


def test_cycle_cli_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """trinity cycle --dry-run previews without writing."""
    config_path = _write_config(tmp_path)

    from trinity import paths

    monkeypatch.setattr(paths, "config_file", lambda: config_path)
    monkeypatch.setattr(paths, "manifest_file", lambda: tmp_path / "manifest.jsonl")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    monkeypatch.setattr(
        "trinity.orchestrator.fetch_wallpaper",
        lambda *_a, **_kw: _fake_fetched(),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["cycle", "--offset", "2", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "offset 2" in result.output


def test_cycle_unsupported_providers_raise_clierror(tmp_path: Path) -> None:
    """apply_to_surfaces with unsupported providers and temporal_offset
    raises CLIError.
    """
    from trinity.cli import CLIError

    for provider in ("solid", "file", "json-api"):
        cfg = _make_cycle_config(tmp_path, provider=provider)
        with pytest.raises(CLIError) as excinfo:
            apply_to_surfaces(
                cfg, manifest=Manifest(), dry_run=False, temporal_offset=2
            )
        assert "does not support temporal cycling" in str(excinfo.value)
        assert excinfo.value.status == 2


def test_cycle_cli_unsupported_provider_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """trinity cycle CLI with unsupported provider exits 2."""
    cfg_dir = tmp_path / "xdg_config" / "trinity"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    shared = str(tmp_path / "shared")
    user_state = str(tmp_path / "user")
    config_path = cfg_dir / "config.toml"
    config_path.write_text(
        f"""[surface]
schema_version = 1
[surface.source]
provider = "solid"
[surface.behaviour]
shared_dir = "{shared}"
user_dir = "{user_state}"
"""
    )

    from trinity import paths
    monkeypatch.setattr(paths, "config_file", lambda: config_path)
    monkeypatch.setattr(paths, "manifest_file", lambda: tmp_path / "manifest.jsonl")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    runner = CliRunner()
    result = runner.invoke(main, ["cycle", "--offset", "2"])
    from trinity.cli import CLIError
    assert isinstance(result.exception, CLIError)
    assert result.exception.status == 2
    assert "does not support temporal cycling" in str(result.exception)
