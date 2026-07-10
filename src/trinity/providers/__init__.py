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


class _BuiltinPlugin:
    """Adapter exposing a built-in provider as a pluggy plugin."""

    def __init__(
        self,
        name: str,
        info: ProviderInfo,
        fetch: Callable[[dict[str, Any]], FetchedImage],
    ) -> None:
        self._name = name
        self._info = info
        self._fetch = fetch

    @hookimpl
    def trinity_provider_name(self) -> str:
        return self._name

    @hookimpl
    def trinity_provider_info(self) -> ProviderInfo:
        return self._info

    @hookimpl
    def trinity_provider_fetch(self, options: dict[str, Any]) -> FetchedImage:
        return self._fetch(options)


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

    from trinity.logging import get_logger

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
    from trinity.providers.builtin import bing, file, solid

    for plugin in (
        _BuiltinPlugin("bing", _BING_INFO, bing.fetch),
        _BuiltinPlugin("file", _FILE_INFO, file.fetch),
        _BuiltinPlugin("solid", _SOLID_INFO, solid.fetch),
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


def fetch_from_source(pm: pluggy.PluginManager, source: Source) -> FetchedImage:
    """Dispatch ``source`` to the appropriate provider and return bytes."""
    plugin = get_provider(pm, source.provider)
    options = dict(source.options.model_dump())
    return _call_fetch(plugin, options)
