"""Pydantic models that mirror ``~/.config/usurface/config.toml``.

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
    inside each provider plugin. We accept any dict here and let the
    registry do its own validation when the provider is invoked.
    """

    model_config = ConfigDict(extra="allow")


class Source(_StrictModel):
    """Where the surface set's wallpaper comes from."""

    provider: str = Field(
        description="Provider plugin name (built-in or entry-point)."
    )
    options: SourceOptions = Field(
        default_factory=SourceOptions,
        description="Provider-specific options.",
    )

    @field_validator("provider")
    @classmethod
    def _check_provider_name(cls, value: str) -> str:
        if not _PROVIDER_RE.match(value):
            raise ValueError(
                f"invalid provider name {value!r}; "
                "must match [a-z][a-z0-9_-]{0,63}"
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


class Login(_StrictModel):
    """Login-screen specific tokens."""

    clock_format: str = Field(default="hh:mm")
    accent_color: str = Field(default="#1d99f3")
    show_user_list: bool = Field(default=True)

    @field_validator("accent_color")
    @classmethod
    def _check_color(cls, value: str) -> str:
        if not _HEX_COLOR_RE.match(value):
            raise ValueError(f"accent_color must be #RGB or #RRGGBB, got {value!r}")
        return value


class Lock(_StrictModel):
    """Lock-screen specific tokens."""

    on_idle_dim_seconds: int = Field(default=10, ge=0, le=600)
    suppress_wake_keypress: bool = Field(default=True)


class Behaviour(_StrictModel):
    """File-layout controls."""

    shared_dir: str = Field(
        default="/usr/local/share/wallpapers",
        description="plasmalogin-visible directory.",
    )
    user_dir: str = Field(
        default="~/.local/state/usurface",
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

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: Any) -> Any:
        """Accept v1 only for now; bail on unknown schema versions."""
        if isinstance(data, dict) and "schema_version" in data:
            sv = data["schema_version"]
            if sv != SCHEMA_VERSION:
                raise ValueError(
                    f"unsupported schema_version={sv}; this build understands "
                    f"version {SCHEMA_VERSION}"
                )
        return data


class Config(_StrictModel):
    """The root configuration document."""

    surface: Surface
