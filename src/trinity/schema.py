"""Pydantic models that mirror ``~/.config/trinity/config.toml``.

Strict by design: typos are common failure modes for TOML config.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 1

# A conservative TOML-friendly colour token: #RGB or #RRGGBB.
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")

# Provider plugin names: lowercase letters, digits, hyphen, underscore.
_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# Font family names: more permissive than providers; allow spaces.
_FONT_FAMILY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,127}$")

# Qt font weight tokens accepted by QML's FontLoader / Text.weight.
# Numeric weights (0-1000) are also valid in QML but the QML patcher writes
# the value into a ``property string fontWeight`` literal, so we accept the
# standard named tokens here. Numeric values are accepted too (as strings).
_FONT_WEIGHT_RE = re.compile(
    r"^(Thin|ExtraLight|Light|Normal|Medium|DemiBold|Bold|ExtraBold|Black"
    r"|100|200|300|400|500|600|700|800|900)$",
    re.IGNORECASE,
)

# Qt date/time format tokens: letters and digits for the format
# characters, common punctuation for separators, and single quotes for
# Qt literal-text sections ('...'). Double quotes, control characters,
# and the empty string are rejected — the value lands inside a QML
# double-quoted string literal, and rejecting early surfaces typos
# instead of silently producing a broken clock.
_CLOCK_FORMAT_RE = re.compile(r"^[A-Za-z0-9 :/\-.,']{1,64}$")


class _StrictModel(BaseModel):
    """Common config: forbid unknown keys and treat all fields as final."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class SourceOptions(_StrictModel):
    """Provider-specific options, validated against the provider schema.

    The provider name is the dispatch key; the actual validation happens
    inside each provider plugin (see ``trinity.providers.builtin.*``).
    We deliberately use ``extra="allow"`` here because the option keys
    are provider-specific — a Bing option set is structurally different
    from a JSON-API option set, and a single TOML table cannot enforce
    per-provider rules. The per-provider schema (each provider declares
    ``trinity_provider_options_schema``) catches typos and unknown keys
    at ``trinity apply`` time. Any third-party provider that does *not*
    declare a schema is permissive by design.
    """

    # Intentional: see the docstring above. Rejecting unknown keys here
    # would require bundling all built-in provider schemas in a single
    # discriminated union, which would force every third-party plugin
    # to ship its schema as a Pydantic model referenced from core code.
    # The current "allow here, validate at fetch time" split keeps
    # third-party plugins plug-and-play while still catching real typos
    # for built-ins.
    model_config = ConfigDict(extra="allow")


class Source(_StrictModel):
    """Where the surface set's wallpaper comes from."""

    provider: str = Field(description="Provider plugin name (built-in or entry-point).")
    options: SourceOptions = Field(
        default_factory=SourceOptions,
        description="Provider-specific options.",
    )

    @field_validator("provider")
    @classmethod
    def _check_provider_name(cls, value: str) -> str:
        if not _PROVIDER_RE.match(value):
            raise ValueError(
                f"invalid provider name {value!r}; must match [a-z][a-z0-9_-]{{0,63}}"
            )
        return value


class Fonts(_StrictModel):
    """Font tokens applied to login + lock screens."""

    family: str = Field(default="Inter", description="Font family name.")
    weight: str = Field(default="Normal", description="Font weight token.")
    password_character: str = Field(
        default="*", min_length=1, max_length=4, description="Mask character."
    )

    @field_validator("family")
    @classmethod
    def _check_family(cls, value: str) -> str:
        if not _FONT_FAMILY_RE.match(value):
            raise ValueError(f"invalid font family name: {value!r}")
        return value

    @field_validator("weight")
    @classmethod
    def _check_weight(cls, value: str) -> str:
        if not _FONT_WEIGHT_RE.match(value):
            raise ValueError(
                f"invalid font weight {value!r}; expected a Qt weight token "
                f"(Thin, Light, Normal, Medium, Bold, Black, ...) or 100-900"
            )
        return value

    @field_validator("password_character")
    @classmethod
    def _check_password_character(cls, value: str) -> str:
        """Reject control characters, newlines, tabs, and double quotes.

        The value lands inside a QML double-quoted string literal, so
        characters that break the literal (double quotes, backslashes
        that form escape sequences unexpectedly) or control characters
        (which produce invisible/unpredictable rendering) are rejected.
        """
        for ch in value:
            code = ord(ch)
            if code < 0x20 or code == 0x7F:
                raise ValueError(
                    f"password_character {value!r} contains control character "
                    f"(U+{code:04X})"
                )
            if ch in ("\n", "\r", "\t", '"', "\\"):
                raise ValueError(
                    f"password_character {value!r} contains character "
                    f"'{ch}' which would break generated QML"
                )
        return value


class Login(_StrictModel):
    """Login-screen specific tokens.

    ``show_user_list`` was removed: the SDDM Breeze theme computes the
    user-list visibility from the user model (``userListModel.count``
    vs ``disableAvatarsThreshold``) and exposes no ``theme.conf`` key
    or QML property for us to rewrite safely. A legacy-key-stripping
    validator tolerates the old key in existing config files.
    """

    clock_format: str = Field(default="hh:mm")
    accent_color: str = Field(default="#1d99f3")

    @field_validator("clock_format")
    @classmethod
    def _check_clock_format(cls, value: str) -> str:
        if not _CLOCK_FORMAT_RE.match(value):
            raise ValueError(
                f"invalid clock_format {value!r}; use Qt date/time tokens "
                f"(e.g. 'hh:mm', 'HH:mm AP')"
            )
        return value

    @field_validator("accent_color")
    @classmethod
    def _check_color(cls, value: str) -> str:
        if not _HEX_COLOR_RE.match(value):
            raise ValueError(f"accent_color must be #RGB or #RRGGBB, got {value!r}")
        return value

    # Removed keys that older config files may still contain. Stripped
    # (with a warning) before validation so existing configs don't fail.
    @model_validator(mode="before")
    @classmethod
    def _strip_removed_keys(cls, data: Any) -> Any:
        removed = ("show_user_list",)
        if isinstance(data, dict) and any(key in data for key in removed):
            # Copy: never mutate the caller's dict (it may be reused).
            data = dict(data)
            for key in removed:
                if key in data:
                    from trinity.logging_setup import get_logger

                    get_logger(__name__).warning(
                        "config_ignored_key",
                        section="surface.login",
                        key=key,
                        hint="removed: SDDM computes user-list visibility internally",
                    )
                    del data[key]
        return data


class Lock(_StrictModel):
    """Lock-screen specific tokens.

    ``suppress_wake_keypress``: when true, the keypress that wakes the
    lock screen is consumed instead of being typed into the password
    field (implemented by patching the password box's ``Keys.onPressed``
    handler in ``MainBlock.qml``).
    """

    on_idle_dim_seconds: int = Field(default=10, ge=0, le=600)
    suppress_wake_keypress: bool = Field(default=True)


# Alignment tokens for clock positioning.
_ALIGN_RE = re.compile(
    r"^(top|bottom|left|right|center"
    r"|top_left|top_right|bottom_left|bottom_right)$"
)


class ClockPosition(_StrictModel):
    """Clock position overrides for SDDM login and lock screen.

    When ``enabled = true``, the QML patcher repositions the clock item
    based on the specified alignment or coordinates.  Layout-managed
    clocks use ``Layout.alignment``; free-floating clocks use
    ``anchors``.

    The patch respects existing dynamic bindings (``visible``,
    ``opacity`` tied to multiscreen pointer detection) and does not
    inject absolute coordinates into layout-managed items.
    """

    enabled: bool = Field(default=False, description="Enable clock repositioning.")
    alignment: str | None = Field(
        default=None,
        description=(
            "Alignment token for layout-managed clocks: top, bottom, "
            "left, right, center, top_left, top_right, bottom_left, "
            "bottom_right."
        ),
    )
    x: int | None = Field(
        default=None, ge=0, description="Absolute X coordinate (free-floating only)."
    )
    y: int | None = Field(
        default=None, ge=0, description="Absolute Y coordinate (free-floating only)."
    )

    @field_validator("alignment")
    @classmethod
    def _check_alignment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _ALIGN_RE.match(value):
            raise ValueError(
                f"invalid clock alignment {value!r}; "
                f"expected one of: top, bottom, left, right, center, "
                f"top_left, top_right, bottom_left, bottom_right"
            )
        return value


class ThemeTokens(_StrictModel):
    """Opt-in switch for the fragile QML-token patching machinery.

    When ``enabled = false`` (the default), ``apply`` skips all QML
    patching and drift checks, and ``install`` skips template extraction.
    The font/lock/login token config sections remain valid but inert —
    a warning is emitted if non-default values are set while the feature
    is disabled, so config isn't silently ignored.

    Tier-1 users (wallpaper-sync only) don't pay the complexity tax for
    the QML-token system; Tier-2 users opt in explicitly.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Enable QML patching and drift detection. Default false: the "
            "simple wallpaper-sync use case skips this fragile machinery."
        ),
    )
    skip_qmllint: bool = Field(
        default=False,
        description=(
            "Skip qmllint post-patch validation. Use at your own risk — "
            "a QML syntax error would cause the greeter to fall back to the "
            "built-in blue locker. Only set this if qmllint is unavailable "
            "and you accept the risk."
        ),
    )
    clock_position: ClockPosition = Field(
        default_factory=lambda: ClockPosition(),
        description="Clock position overrides for SDDM and lock screen.",
    )


class Behaviour(_StrictModel):
    """File-layout controls."""

    shared_dir: str = Field(
        default="/usr/local/share/wallpapers",
        description="plasmalogin-visible directory.",
    )
    user_dir: str = Field(
        default="~/.local/state/trinity",
        description="per-user canonical copy directory.",
    )


class Surface(_StrictModel):
    """Top-level wrapper around the surface set."""

    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)
    source: Source
    fonts: Fonts = Field(default_factory=Fonts)
    login: Login = Field(default_factory=Login)
    lock: Lock = Field(default_factory=Lock)
    behaviour: Behaviour = Field(default_factory=Behaviour)
    theme_tokens: ThemeTokens = Field(default_factory=ThemeTokens)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: Any) -> Any:
        """Accept v1 configs and migrate from "always on" to opt-in.

        Pre-Phase-2 configs had theme-token patching always on with no
        switch.  We auto-migrate to ``theme_tokens.enabled = true`` for
        any loaded config that *doesn't* declare the key and has the
        v1 schema version, so existing users don't silently lose
        patching.  New configs written by ``config init`` opt out by
        default — Tier-1 users don't pay the complexity tax.  A
        structured deprecation log explains the migration.
        """
        if isinstance(data, dict):
            if "schema_version" in data:
                sv = data["schema_version"]
                if sv != SCHEMA_VERSION:
                    raise ValueError(
                        f"unsupported schema_version={sv}; this build understands "
                        f"version {SCHEMA_VERSION}"
                    )
            # Auto-migrate pre-Phase-2 configs: enable tokens unless
            # the user has explicitly opted out.
            if "theme_tokens" not in data:
                data = dict(data)
                data["theme_tokens"] = {"enabled": True}
                from trinity.logging_setup import get_logger

                get_logger(__name__).warning(
                    "config_theme_tokens_migrated",
                    hint=(
                        "theme_tokens is now opt-in; this existing config was "
                        "auto-migrated to enabled=true. Set "
                        "[surface.theme_tokens] enabled=false in config to opt "
                        "out (the font/lock/login token sections become inert)."
                    ),
                )
        return data


class Config(_StrictModel):
    """The root configuration document."""

    surface: Surface
