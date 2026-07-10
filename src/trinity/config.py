"""TOML config loader/dumper.

Wraps :mod:`trinity.schema` so the CLI can ``load_config(path)`` and
``dump_config(model, path)`` without touching pydantic directly.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from trinity import paths
from trinity.atomic import atomic_write_text
from trinity.schema import Config as Config


def _expand(path: str) -> str:
    """Expand ``~`` and env vars in a path string.

    When running via sudo, ``~`` must expand to the *invoking* user's
    home directory, not ``/root``. We detect that case and substitute the
    original user's home before the normal expansion.
    """
    sudo_home = _sudo_user_home()
    if sudo_home is not None and path.startswith("~"):
        # ``os.path.expanduser`` only looks at $HOME; for sudo it points at
        # /root. Replace a leading ``~`` with the real user's home.
        rest = path[1:]
        if rest.startswith("/") or rest == "":
            path = str(sudo_home) + rest
    return os.path.expandvars(os.path.expanduser(path))


def _sudo_user_home() -> Path | None:
    """Return the invoking user's home when running as root via sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and os.geteuid() == 0:
        import pwd

        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return None


def load_config(path: Path | None = None) -> Config:
    """Load and validate the config file at ``path`` (default location).

    After the pydantic schema validates the TOML, the selected provider's
    options schema (if it declares one) validates ``surface.source.options``.
    This catches option typos at config-load time (e.g. ``resoultion``
    instead of ``resolution``) rather than at fetch time (3am timer).
    """
    cfg_path = Path(path) if path is not None else paths.config_file()
    if not cfg_path.exists():
        raise FileNotFoundError(f"config file not found: {cfg_path}")
    with cfg_path.open("rb") as f:
        raw: dict[str, Any] = tomllib.load(f)
    cfg = Config.model_validate(raw)
    # Validate provider options against the provider's declared schema.
    _validate_provider_options_or_raise(cfg, cfg_path)
    return cfg


def _validate_provider_options_or_raise(cfg: Config, cfg_path: Path) -> None:
    """Validate provider options; raise FileNotFoundError-like error on failure."""
    from trinity.providers import make_plugin_manager, validate_provider_options

    pm = make_plugin_manager()
    try:
        validate_provider_options(pm, cfg.surface.source)
    except ValueError as exc:
        raise ValueError(f"{cfg_path}: {exc}") from exc


def load_config_from_string(toml_text: str) -> Config:
    """Parse and validate config from a TOML string (used by tests)."""
    raw: dict[str, Any] = tomllib.loads(toml_text)
    return Config.model_validate(raw)


def dump_config(config: Config, path: Path | None = None) -> Path:
    """Atomically write ``config`` as TOML to ``path`` (default location)."""
    cfg_path = Path(path) if path is not None else paths.config_file()
    text = _to_toml(config.model_dump(mode="json"))
    return atomic_write_text(cfg_path, text, mode=0o644)


def expand_behaviour_paths(config: Config) -> Config:
    """Return a copy of ``config`` with ``~`` and env vars expanded.

    Useful for the orchestrator so it doesn't need to know about
    expansion semantics.
    """
    new_behaviour = config.surface.behaviour.model_copy(
        update={
            "shared_dir": _expand(config.surface.behaviour.shared_dir),
            "user_dir": _expand(config.surface.behaviour.user_dir),
        }
    )
    new_surface = config.surface.model_copy(update={"behaviour": new_behaviour})
    return config.model_copy(update={"surface": new_surface})


def _to_toml(data: Any, prefix: str = "") -> str:
    """Tiny TOML serializer for pydantic primitives.

    Emits a ``[section]`` for each dict, with primitive key=value pairs
    first, then nested sections as their own ``[a.b.c]`` blocks.
    """
    lines: list[str] = []
    sub_sections: list[tuple[str, dict[str, Any]]] = []
    for key, value in data.items():
        if isinstance(value, dict):
            section_key = f"{prefix}.{key}" if prefix else key
            sub_sections.append((section_key, value))
        elif value is None:
            # TOML has no null; omitting the key round-trips back to the
            # model default, whereas writing "" would change the type.
            continue
        else:
            lines.append(f"{key} = {_toml_literal(value)}")
    out = "\n".join(lines)
    for section_key, section_value in sub_sections:
        if out:
            out += "\n\n"
        out += f"[{section_key}]\n"
        out += _to_toml(section_value, prefix=section_key)
    return out + "\n"


def _toml_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_literal(v) for v in value) + "]"
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")
