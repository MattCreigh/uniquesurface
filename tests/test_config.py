"""Tests for the TOML config loader."""

from __future__ import annotations

import pytest

from usurface import config


SAMPLE_TOML = """\
[surface]
schema_version = 1

[surface.source]
provider = "bing"

[surface.source.options]
mkt = "en-US"
resolution = "1920x1080"

[surface.fonts]
family = "Inter"
weight = "Normal"
password_character = "*"

[surface.login]
clock_format = "hh:mm"
accent_color = "#1d99f3"
show_user_list = true

[surface.lock]
on_idle_dim_seconds = 10
suppress_wake_keypress = true

[surface.behaviour]
shared_dir = "/usr/local/share/wallpapers"
user_dir = "~/.local/state/usurface"
"""


def test_parses_minimal_config() -> None:
    parsed = config.load_config_from_string(SAMPLE_TOML)
    assert parsed.surface.source.provider == "bing"
    assert parsed.surface.source.options.model_extra == {
        "mkt": "en-US",
        "resolution": "1920x1080",
    }
    assert parsed.surface.fonts.family == "Inter"
    assert parsed.surface.fonts.password_character == "*"
    assert parsed.surface.login.accent_color == "#1d99f3"
    assert parsed.surface.lock.on_idle_dim_seconds == 10
    assert parsed.surface.lock.suppress_wake_keypress is True


def test_rejects_unknown_top_level_key() -> None:
    with pytest.raises(Exception):  # noqa: PT011
        config.load_config_from_string(SAMPLE_TOML + "\n[surface.unknown]\nfoo = 1\n")


def test_rejects_invalid_provider_name() -> None:
    bad = SAMPLE_TOML.replace('provider = "bing"', "provider = 'Bing!'")
    with pytest.raises(Exception):  # noqa: PT011
        config.load_config_from_string(bad)


def test_rejects_invalid_accent_color() -> None:
    bad = SAMPLE_TOML.replace('accent_color = "#1d99f3"', 'accent_color = "blue"')
    with pytest.raises(Exception):  # noqa: PT011
        config.load_config_from_string(bad)


def test_rejects_invalid_password_character() -> None:
    bad = SAMPLE_TOML.replace('password_character = "*"', 'password_character = ""')
    with pytest.raises(Exception):  # noqa: PT011
        config.load_config_from_string(bad)


def test_rejects_unknown_schema_version() -> None:
    bad = SAMPLE_TOML.replace("schema_version = 1", "schema_version = 99")
    with pytest.raises(Exception, match="schema_version"):
        config.load_config_from_string(bad)


def test_dump_and_reload_round_trip() -> None:
    parsed = config.load_config_from_string(SAMPLE_TOML)
    dumped = config._to_toml(parsed.model_dump(mode="json"))  # type: ignore[attr-defined]
    reparsed = config.load_config_from_string(dumped)
    assert reparsed == parsed


def test_expand_behaviour_paths() -> None:
    parsed = config.load_config_from_string(SAMPLE_TOML)
    expanded = config.expand_behaviour_paths(parsed)
    assert "~" not in expanded.surface.behaviour.user_dir
    assert expanded.surface.behaviour.user_dir.endswith(".local/state/usurface")


def test_missing_source_provider_fails() -> None:
    bad = """\
[surface]
schema_version = 1
"""
    with pytest.raises(Exception):  # noqa: PT011
        config.load_config_from_string(bad)
