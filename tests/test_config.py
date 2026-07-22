"""Tests for the TOML config loader."""

from __future__ import annotations

import pytest

from trinity import config

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
user_dir = "~/.local/state/trinity"
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
    with pytest.raises(Exception):
        config.load_config_from_string(SAMPLE_TOML + "\n[surface.unknown]\nfoo = 1\n")


def test_rejects_invalid_provider_name() -> None:
    bad = SAMPLE_TOML.replace('provider = "bing"', "provider = 'Bing!'")
    with pytest.raises(Exception):
        config.load_config_from_string(bad)


def test_rejects_invalid_accent_color() -> None:
    bad = SAMPLE_TOML.replace('accent_color = "#1d99f3"', 'accent_color = "blue"')
    with pytest.raises(Exception):
        config.load_config_from_string(bad)


def test_rejects_invalid_password_character() -> None:
    bad = SAMPLE_TOML.replace('password_character = "*"', 'password_character = ""')
    with pytest.raises(Exception):
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


def test_dump_omits_none_values() -> None:
    """TOML has no null: None-valued keys are omitted, never written as ''."""
    dumped = config._to_toml(  # type: ignore[attr-defined]
        {"section": {"kept": "x", "dropped": None}}
    )
    assert "kept" in dumped
    assert "dropped" not in dumped
    assert '""' not in dumped


def test_expand_behaviour_paths() -> None:
    parsed = config.load_config_from_string(SAMPLE_TOML)
    expanded = config.expand_behaviour_paths(parsed)
    assert "~" not in expanded.surface.behaviour.user_dir
    assert expanded.surface.behaviour.user_dir.endswith(".local/state/trinity")


def test_missing_source_provider_fails() -> None:
    bad = """\
[surface]
schema_version = 1
"""
    with pytest.raises(Exception):
        config.load_config_from_string(bad)


# --- Appendix A: legacy key tolerance + unknown key rejection ---


def test_legacy_show_user_list_still_loads_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A config containing the removed 'show_user_list' key must still
    load (the validator strips it) rather than failing with extra=forbid."""
    import logging

    from trinity.config import load_config_from_string

    toml = """
[surface]
schema_version = 1
[surface.source]
provider = "bing"
[surface.login]
clock_format = "hh:mm"
accent_color = "#1d99f3"
show_user_list = false
"""
    with caplog.at_level(logging.WARNING):
        cfg = load_config_from_string(toml)
    # The field is gone from the model; the config still loads.
    assert cfg.surface.login.clock_format == "hh:mm"
    assert not hasattr(cfg.surface.login, "show_user_list")


def test_unknown_key_still_fails_validation() -> None:
    """A genuinely unknown key must still be rejected (extra=forbid)."""
    import pytest

    from trinity.config import load_config_from_string

    toml = """
[surface]
schema_version = 1
bogus_key = true
[surface.source]
provider = "bing"
"""
    with pytest.raises(Exception, match=r"extra|bogus_key"):
        load_config_from_string(toml)


# --- password character validation (Phase 2.5) -------------------------


def test_password_character_rejects_newline() -> None:
    """Newlines in password_character are rejected (would break QML)."""
    bad = SAMPLE_TOML.replace('password_character = "*"', 'password_character = "\\n"')
    with pytest.raises(Exception, match="password_character"):
        config.load_config_from_string(bad)


def test_password_character_rejects_control_char() -> None:
    """Control characters (tab, NUL, etc.) in password_character are rejected."""
    bad = SAMPLE_TOML.replace('password_character = "*"', 'password_character = "\\t"')
    with pytest.raises(Exception, match="password_character"):
        config.load_config_from_string(bad)


def test_password_character_rejects_double_quote() -> None:
    """Double quotes in password_character are rejected (break QML string literal)."""
    bad = SAMPLE_TOML.replace('password_character = "*"', 'password_character = "\\""')
    with pytest.raises(Exception, match="password_character"):
        config.load_config_from_string(bad)


def test_password_character_accepts_valid_chars() -> None:
    """Normal mask characters are accepted."""
    for char in ["●", "•", "*", "x", "ab"]:
        toml = SAMPLE_TOML.replace(
            'password_character = "*"',
            f'password_character = "{char}"',
        )
        cfg = config.load_config_from_string(toml)
        assert cfg.surface.fonts.password_character == char
