"""Provider plugin system.

A provider returns the bytes of an image suitable for use as the
desktop/lock/login wallpaper. Built-ins ship under
:mod:`trinity.providers.builtin`. Third-party providers are loaded via
``pluggy`` entry points declared under the ``trinity.providers`` group.

Security note: third-party providers run as the invoking user and may
read network resources. Treat the entry-point group as a supply-chain
surface and only install providers you trust (see ``README.md`` in this
package).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

import pluggy
import pydantic
from pydantic import BaseModel

if TYPE_CHECKING:
    from trinity.schema import Source

hookspec = pluggy.HookspecMarker("trinity")
hookimpl = pluggy.HookimplMarker("trinity")


@dataclass(frozen=True)
class ProviderInfo:
    """Metadata for one provider plugin."""

    name: str
    description: str
    builtin: bool


@dataclass(frozen=True)
class FetchedImage:
    """Result of a provider fetch."""

    data: bytes
    content_type: str
    suggested_extension: str


class ProviderError(RuntimeError):
    """A provider failed to fetch or generate an image."""


class ProviderPlugin(Protocol):
    """Structural interface a provider plugin must satisfy."""

    def trinity_provider_name(self) -> str: ...

    def trinity_provider_info(self) -> ProviderInfo: ...

    def trinity_provider_fetch(self, options: dict[str, Any]) -> FetchedImage: ...


class ProviderHooks:
    """Hookspecs for wallpaper providers."""

    @hookspec
    def trinity_provider_name(self) -> str:
        """Return the short provider name (matches ``[surface.source].provider``)."""
        raise NotImplementedError

    @hookspec
    def trinity_provider_info(self) -> ProviderInfo:
        """Return metadata describing this provider."""
        raise NotImplementedError

    @hookspec(firstresult=True)
    def trinity_provider_fetch(self, options: dict[str, Any]) -> FetchedImage:
        """Fetch or generate an image; return its bytes."""
        raise NotImplementedError

    @hookspec(firstresult=True)
    def trinity_provider_options_schema(self) -> type[BaseModel] | None:
        """Return a pydantic model class validating this provider's options.

        Returning ``None`` (or not implementing the hook) falls back to
        the permissive ``SourceOptions`` behaviour — all keys accepted,
        no validation.  Built-in providers always return a schema.
        """
        raise NotImplementedError

    @hookspec(firstresult=True)
    def trinity_provider_probe(self, options: dict[str, Any]) -> str | None:
        """Return an opaque change token for the source, cheaply.

        A probe must be much cheaper than a full fetch (a metadata-only
        request, a local ``stat``, ...).  Two probes returning the same
        token mean the image is unchanged, so ``apply --if-changed`` can
        skip the download and the surface writes entirely.

        Returning ``None`` (or not implementing the hook) means the
        provider cannot probe; callers fall back to a full fetch.
        Tokens are opaque — callers only ever compare them for equality.
        """
        raise NotImplementedError


class _BuiltinPlugin:
    """Adapter exposing a built-in provider as a pluggy plugin."""

    def __init__(
        self,
        name: str,
        info: ProviderInfo,
        fetch: Callable[[dict[str, Any]], FetchedImage],
        options_schema: type[BaseModel] | None = None,
        probe: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> None:
        self._name = name
        self._info = info
        self._fetch = fetch
        self._options_schema = options_schema
        self._probe = probe

    @hookimpl
    def trinity_provider_name(self) -> str:
        return self._name

    @hookimpl
    def trinity_provider_info(self) -> ProviderInfo:
        return self._info

    @hookimpl
    def trinity_provider_fetch(self, options: dict[str, Any]) -> FetchedImage:
        return self._fetch(options)

    @hookimpl
    def trinity_provider_options_schema(self) -> type[BaseModel] | None:
        return self._options_schema

    @hookimpl
    def trinity_provider_probe(self, options: dict[str, Any]) -> str | None:
        if self._probe is None:
            return None
        return self._probe(options)


_BING_INFO = ProviderInfo(
    name="bing",
    description="Bing Picture of the Day.",
    builtin=True,
)
_FILE_INFO = ProviderInfo(
    name="file",
    description="Local image file.",
    builtin=True,
)
_SOLID_INFO = ProviderInfo(
    name="solid",
    description="Solid colour or gradient.",
    builtin=True,
)
_JSON_API_INFO = ProviderInfo(
    name="json-api",
    description="Generic HTTPS JSON-metadata → image URL recipe.",
    builtin=True,
)
_RSS_INFO = ProviderInfo(
    name="rss",
    description="RSS 2.0 / Atom image feed (enclosure or Media RSS).",
    builtin=True,
)


def make_plugin_manager() -> pluggy.PluginManager:
    """Return a configured plugin manager with all built-ins registered.

    Third-party providers are loaded via the ``trinity.providers``
    setuptools entry-point group so users can install a pip/uv package
    that declares an entry point and select it via ``[surface.source]
    provider = "<name>"``. Third-party providers run as the invoking
    user and may read network resources — treat the entry-point group as
    a supply-chain surface and only install providers you trust (see
    ``providers/README.md``).
    """
    pm = pluggy.PluginManager("trinity")
    pm.add_hookspecs(ProviderHooks)
    _register_builtins(pm)
    _register_entry_point_plugins(pm)
    return pm


def _register_entry_point_plugins(pm: pluggy.PluginManager) -> None:
    """Register any third-party provider plugins discovered via the
    ``trinity.providers`` setuptools entry-point group.

    Discovery failures (a package declares the entry point but the
    object cannot be imported) are logged at warning level and skipped
    so one broken plugin cannot prevent the built-ins from working.
    """
    from importlib.metadata import entry_points

    from trinity.logging_setup import get_logger

    log = get_logger(__name__)
    for ep in entry_points(group="trinity.providers"):
        try:
            plugin = ep.load()
        except Exception as exc:
            log.warning(
                "provider_entry_point_load_failed",
                entry_point=ep.name,
                error=str(exc),
            )
            continue
        try:
            pm.register(plugin, name=ep.name)
            log.info("provider_entry_point_loaded", entry_point=ep.name)
        except Exception as exc:
            log.warning(
                "provider_entry_point_register_failed",
                entry_point=ep.name,
                error=str(exc),
            )


def _register_builtins(pm: pluggy.PluginManager) -> None:
    """Register the built-in providers shipped in this package."""
    # Imported lazily to avoid a circular dependency on schema.
    from trinity.providers.builtin import bing, file, json_api, rss, solid

    for plugin in (
        _BuiltinPlugin("bing", _BING_INFO, bing.fetch, bing.BingOptions, bing.probe),
        _BuiltinPlugin("file", _FILE_INFO, file.fetch, file.FileOptions, file.probe),
        _BuiltinPlugin(
            "solid", _SOLID_INFO, solid.fetch, solid.SolidOptions, solid.probe
        ),
        _BuiltinPlugin(
            "json-api",
            _JSON_API_INFO,
            json_api.fetch,
            json_api.JsonApiOptions,
            json_api.probe,
        ),
        _BuiltinPlugin("rss", _RSS_INFO, rss.fetch, rss.RssOptions, rss.probe),
    ):
        pm.register(plugin)


def _call_name(plugin: Any) -> str:
    """Call the name hook on one plugin and return its name."""
    name_fn = getattr(plugin, "trinity_provider_name", None)
    if name_fn is None:
        raise AttributeError(f"{plugin!r} has no trinity_provider_name hook")
    return cast(str, name_fn())


def _call_info(plugin: Any) -> ProviderInfo:
    info_fn = getattr(plugin, "trinity_provider_info", None)
    if info_fn is None:
        raise AttributeError(f"{plugin!r} has no trinity_provider_info hook")
    return cast(ProviderInfo, info_fn())


def _call_fetch(plugin: Any, options: dict[str, Any]) -> FetchedImage:
    fetch_fn = getattr(plugin, "trinity_provider_fetch", None)
    if fetch_fn is None:
        raise AttributeError(f"{plugin!r} has no trinity_provider_fetch hook")
    return cast(FetchedImage, fetch_fn(options))


def _call_probe(plugin: Any, options: dict[str, Any]) -> str | None:
    """Call the probe hook on one plugin; None if the plugin has none."""
    probe_fn = getattr(plugin, "trinity_provider_probe", None)
    if probe_fn is None:
        return None
    return cast("str | None", probe_fn(options))


def get_provider(pm: pluggy.PluginManager, name: str) -> Any:
    """Return the plugin whose ``trinity_provider_name`` matches ``name``."""
    for plugin in pm.get_plugins():
        if _call_name(plugin) == name:
            return plugin
    raise KeyError(f"no provider registered with name {name!r}")


def list_providers(pm: pluggy.PluginManager) -> list[ProviderInfo]:
    """Return metadata for every registered provider."""
    infos: list[ProviderInfo] = []
    for plugin in pm.get_plugins():
        infos.append(_call_info(plugin))
    infos.sort(key=lambda i: (not i.builtin, i.name))
    return infos


def get_provider_options_schema(
    pm: pluggy.PluginManager, name: str
) -> type[BaseModel] | None:
    """Return the pydantic options-schema class for the named provider.

    Returns ``None`` if the provider does not implement the
    ``trinity_provider_options_schema`` hook (third-party fallback).
    """
    plugin = get_provider(pm, name)
    schema_fn = getattr(plugin, "trinity_provider_options_schema", None)
    if schema_fn is None:
        return None
    return cast("type[BaseModel] | None", schema_fn())


def validate_provider_options(
    pm: pluggy.PluginManager, source: Source
) -> dict[str, Any] | None:
    """Validate ``source.options`` against the provider's declared schema.

    Returns the validated options as a dict, or ``None`` if the provider
    does not declare a schema (third-party fallback — permissive).

    Raises ``ValueError`` with a clear message naming the provider and
    the offending field(s) if validation fails.
    """
    try:
        schema_cls = get_provider_options_schema(pm, source.provider)
    except KeyError:
        raise ValueError(
            f"unknown provider '{source.provider}'; run 'trinity provider list'"
        ) from None
    if schema_cls is None:
        # Third-party provider without a schema hook: fall back to the
        # permissive behaviour (all keys accepted, no validation).
        from trinity.logging_setup import get_logger

        log = get_logger(__name__)
        log.warning(
            "provider_options_schema_missing",
            provider=source.provider,
            hint="third-party provider does not declare an options schema; "
            "options are not validated",
        )
        return None
    raw = dict(source.options.model_dump())
    try:
        validated = schema_cls.model_validate(raw)
    except (pydantic.ValidationError, ValueError, TypeError) as exc:
        # Pydantic's ValidationError is the common case (unknown key,
        # wrong type, out-of-range value). ValueError/TypeError cover
        # custom validators in third-party providers. Anything else
        # (KeyError, NameError, …) indicates a bug and propagates so it
        # shows up in the user's terminal as a real traceback rather
        # than being silently rewritten as a "rejected options" message.
        raise ValueError(
            f"provider '{source.provider}' rejected options: {exc}"
        ) from exc
    return validated.model_dump()


def fetch_from_source(pm: pluggy.PluginManager, source: Source) -> FetchedImage:
    """Dispatch ``source`` to the appropriate provider and return bytes."""
    try:
        plugin = get_provider(pm, source.provider)
    except KeyError:
        raise ProviderError(
            f"unknown provider '{source.provider}'; run 'trinity provider list'"
        ) from None
    # Try schema-validated options first; fall back to raw dump if the
    # provider has no schema (backward-compatible).
    validated = validate_provider_options(pm, source)
    options = validated if validated is not None else dict(source.options.model_dump())
    return _call_fetch(plugin, options)


def probe_from_source(pm: pluggy.PluginManager, source: Source) -> str | None:
    """Return the provider's change token for ``source``, or ``None``.

    ``None`` means the provider cannot probe cheaply (no hook, or the
    hook declined); callers fall back to a full fetch.  Provider
    failures propagate as :class:`ProviderError` — callers decide
    whether a failed probe blocks or degrades to a full fetch.
    """
    try:
        plugin = get_provider(pm, source.provider)
    except KeyError:
        raise ProviderError(
            f"unknown provider '{source.provider}'; run 'trinity provider list'"
        ) from None
    validated = validate_provider_options(pm, source)
    options = validated if validated is not None else dict(source.options.model_dump())
    return _call_probe(plugin, options)
