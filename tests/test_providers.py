"""Tests for the provider registry and built-in providers."""

from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest
import respx

from trinity.providers import (
    ProviderError,
    fetch_from_source,
    get_provider,
    list_providers,
    make_plugin_manager,
)
from trinity.providers.builtin import bing, file, solid
from trinity.schema import Source, SourceOptions

# --- registry ---------------------------------------------------------


def test_registry_registers_builtins() -> None:
    pm = make_plugin_manager()
    infos = list_providers(pm)
    names = {i.name for i in infos}
    assert names == {"bing", "file", "json-api", "rss", "solid"}


def test_third_party_entry_point_plugin_is_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A package declaring a ``trinity.providers`` entry point is loaded
    by ``make_plugin_manager`` and appears alongside the built-ins."""
    from trinity.providers import (
        FetchedImage,
        ProviderInfo,
        _BuiltinPlugin,
        list_providers,
        make_plugin_manager,
    )

    class FakeEntryPoint:
        def __init__(self, name: str, plugin: object) -> None:
            self.name = name
            self._plugin = plugin

        def load(self) -> object:
            return self._plugin

    plugin = _BuiltinPlugin(
        "my-plugin",
        ProviderInfo(
            name="my-plugin",
            description="A fake third-party provider.",
            builtin=False,
        ),
        lambda options: FetchedImage(
            data=b"\xff\xd8\xff" + b"x",
            content_type="image/jpeg",
            suggested_extension=".jpg",
        ),
    )
    fake_eps = [FakeEntryPoint("my-plugin", plugin)]

    # importlib.metadata.entry_points(group=...) returns a list; patch it.
    import importlib.metadata as ilm

    def fake_entry_points(group=None):  # type: ignore[no-untyped-def]
        if group == "trinity.providers":
            return fake_eps
        return []

    monkeypatch.setattr(ilm, "entry_points", fake_entry_points)

    pm = make_plugin_manager()
    names = {i.name for i in list_providers(pm)}
    assert "my-plugin" in names
    assert "bing" in names  # built-ins still registered


def test_broken_entry_point_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A third-party entry point that raises on load is logged and skipped;
    the built-ins still register so a broken plugin cannot brick apply."""

    class BrokenEntryPoint:
        name = "broken-plugin"

        def load(self) -> object:
            raise ImportError("broken plugin")

    import importlib.metadata as ilm

    def fake_entry_points(group=None):  # type: ignore[no-untyped-def]
        if group == "trinity.providers":
            return [BrokenEntryPoint()]
        return []

    monkeypatch.setattr(ilm, "entry_points", fake_entry_points)

    pm = make_plugin_manager()
    names = {i.name for i in list_providers(pm)}
    assert "broken-plugin" not in names
    assert {"bing", "file", "json-api", "rss", "solid"} == names


def test_get_provider_returns_matching_plugin() -> None:
    pm = make_plugin_manager()
    plugin = get_provider(pm, "solid")
    name = pm.hook.trinity_provider_name(plugin=plugin)
    assert "solid" in name


def test_get_provider_raises_for_unknown_name() -> None:
    pm = make_plugin_manager()
    with pytest.raises(KeyError):
        get_provider(pm, "no-such-provider")


def test_fetch_from_source_dispatches_correctly() -> None:
    pm = make_plugin_manager()
    source = Source(
        provider="solid",
        options=SourceOptions.model_validate(
            {"color": "#abcdef", "width": 32, "height": 18}
        ),
    )
    img = fetch_from_source(pm, source)
    assert img.content_type == "image/jpeg"
    assert img.suggested_extension == ".jpg"
    assert img.data[:3] == b"\xff\xd8\xff"  # JPEG SOI


# --- solid -------------------------------------------------------------


def test_solid_generates_jpeg() -> None:
    img = solid.fetch({"color": "#123456", "width": 16, "height": 16})
    assert img.content_type == "image/jpeg"
    assert img.data[:3] == b"\xff\xd8\xff"


def test_solid_short_hex_expanded() -> None:
    img = solid.fetch({"color": "#abc", "width": 8, "height": 8})
    assert img.data[:3] == b"\xff\xd8\xff"


def test_solid_rejects_invalid_color() -> None:
    with pytest.raises(ProviderError):
        solid.fetch({"color": "red", "width": 8, "height": 8})


def test_solid_gradient() -> None:
    img = solid.fetch(
        {"color": "#000000", "gradient_to": "#ffffff", "width": 16, "height": 16}
    )
    assert img.data[:3] == b"\xff\xd8\xff"


def test_solid_rejects_zero_dimensions() -> None:
    """Zero dimensions are caught by the SolidOptions schema (gt=0)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        solid.SolidOptions(color="#000000", width=0, height=0)


def test_solid_rejects_non_numeric_dimensions() -> None:
    """Non-numeric options are caught by the SolidOptions schema (int type)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        solid.SolidOptions(color="#000000", width="wide", height=8)


def test_solid_rejects_oversize_dimensions() -> None:
    """Oversize dimensions are caught by the SolidOptions schema."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        solid.SolidOptions(width=100_000, height=8)


# --- file --------------------------------------------------------------


def test_file_provider_reads_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "wp.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake-payload")
    # The H3 security check allows the runtime shared wallpaper dir;
    # point it at tmp_path so the test is hermetic.
    monkeypatch.setenv("TRINITY_SHARED_DIR", str(tmp_path))
    img = file.fetch({"path": str(target)})
    assert img.content_type == "image/png"
    assert img.suggested_extension == ".png"


def test_file_provider_requires_path() -> None:
    with pytest.raises(ProviderError):
        file.fetch({})


def test_file_provider_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ProviderError):
        file.fetch({"path": str(tmp_path / "nope.png")})


def test_file_provider_expands_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"\xff\xd8\xff" + b"jpegs")
    # Make the test's tmp_path an allowed root via the runtime shared dir.
    monkeypatch.setenv("TRINITY_SHARED_DIR", str(tmp_path))
    img = file.fetch({"path": "~/wp.jpg"})
    assert img.content_type == "image/jpeg"


def test_file_provider_rejects_oversize_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file over the size cap is refused before being read into memory."""
    monkeypatch.setenv("TRINITY_SHARED_DIR", str(tmp_path))
    target = tmp_path / "huge.jpg"
    target.write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(file, "_MAX_LOCAL_BYTES", 2)
    with pytest.raises(ProviderError, match="local-file cap"):
        file.fetch({"path": str(target)})


def test_file_provider_error_does_not_reveal_outside_existence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allow-list rejection fires before the existence check, so the
    message is identical whether or not the outside path exists."""
    monkeypatch.setenv("TRINITY_SHARED_DIR", str(tmp_path / "elsewhere"))
    existing = tmp_path / "real.jpg"
    existing.write_bytes(b"\xff\xd8\xff")
    with pytest.raises(ProviderError, match="not under an allowed root"):
        file.fetch({"path": str(existing)})
    with pytest.raises(ProviderError, match="not under an allowed root"):
        file.fetch({"path": str(tmp_path / "ghost.jpg")})


def test_file_provider_rejects_path_outside_allowed_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user-supplied path that resolves outside ~/Pictures, ~/Wallpapers,
    the system wallpaper dirs, or the runtime shared dir must be rejected
    before any bytes are read into memory."""
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"\xff\xd8\xff" + b"jpegs")
    # Point TRINITY_SHARED_DIR elsewhere so tmp_path is not on the
    # allow-list, then confirm the provider refuses to read it.
    monkeypatch.setenv("TRINITY_SHARED_DIR", str(tmp_path / "elsewhere"))
    with pytest.raises(ProviderError, match="not under an allowed root"):
        file.fetch({"path": str(target)})


# --- bing --------------------------------------------------------------


def test_bing_fetches_metadata_then_image(respx_mock: respx.router.MockRouter) -> None:
    image_bytes = b"\xff\xd8\xff" + b"bing-image-data"

    metadata_route = respx_mock.get(bing._METADATA_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "images": [
                    {
                        "url": "/th?id=OHR.Foo_1920x1080.jpg&pid=hp",
                        "copyright": "Foo",
                    }
                ]
            },
        )
    )
    image_route = respx_mock.get(
        "https://www.bing.com/th?id=OHR.Foo_1920x1080.jpg&pid=hp"
    ).mock(
        return_value=httpx.Response(
            200, content=image_bytes, headers={"content-type": "image/jpeg"}
        )
    )

    img = bing.fetch({"mkt": "en-US", "resolution": "1920x1080"})
    assert metadata_route.called
    assert image_route.called
    assert img.data == image_bytes
    assert img.content_type == "image/jpeg"


def test_bing_replaces_resolution_placeholder(
    respx_mock: respx.router.MockRouter,
) -> None:
    image_bytes = b"\xff\xd8\xff" + b"image"

    respx_mock.get(bing._METADATA_URL).mock(
        return_value=httpx.Response(
            200,
            json={"images": [{"url": "/th?id={resolution}.jpg"}]},
        )
    )
    respx_mock.get("https://www.bing.com/th?id=3840x2160.jpg").mock(
        return_value=httpx.Response(
            200, content=image_bytes, headers={"content-type": "image/jpeg"}
        )
    )

    img = bing.fetch({"resolution": "3840x2160"})
    assert img.data == image_bytes


def test_bing_rejects_unexpected_metadata(respx_mock: respx.router.MockRouter) -> None:
    respx_mock.get(bing._METADATA_URL).mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    with pytest.raises(ProviderError):
        bing.fetch({})


def test_bing_wraps_network_errors(respx_mock: respx.router.MockRouter) -> None:
    """Connection failures surface as ProviderError, not raw httpx errors."""
    respx_mock.get(bing._METADATA_URL).mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(ProviderError, match="HTTP request failed"):
        bing.fetch({})


def test_bing_wraps_http_status_errors(respx_mock: respx.router.MockRouter) -> None:
    """A 5xx from Bing surfaces as ProviderError."""
    respx_mock.get(bing._METADATA_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderError, match="HTTP 503"):
        bing.fetch({})


def test_bing_rejects_invalid_json_metadata(
    respx_mock: respx.router.MockRouter,
) -> None:
    respx_mock.get(bing._METADATA_URL).mock(
        return_value=httpx.Response(200, content=b"<html>not json</html>")
    )
    with pytest.raises(ProviderError, match="not valid JSON"):
        bing.fetch({})


def test_bing_rejects_non_numeric_options() -> None:
    """Non-numeric timeout/index are caught by the BingOptions schema."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        bing.BingOptions(timeout="soon")


# --- bing download size cap (item 8) ---


def test_bing_rejects_oversize_via_content_length(
    respx_mock: respx.router.MockRouter,
) -> None:
    """If Content-Length exceeds _MAX_IMAGE_BYTES, raise ProviderError."""
    respx_mock.get(bing._METADATA_URL).mock(
        return_value=httpx.Response(
            200,
            json={"images": [{"url": "/th?id=big.jpg"}]},
        )
    )
    respx_mock.get("https://www.bing.com/th?id=big.jpg").mock(
        return_value=httpx.Response(
            200,
            content=b"\x00" * 1024,
            headers={
                "content-type": "image/jpeg",
                "content-length": str(bing._MAX_IMAGE_BYTES + 1),
            },
        )
    )
    with pytest.raises(ProviderError, match="download cap"):
        bing.fetch({})


def test_bing_rejects_oversize_while_streaming(
    respx_mock: respx.router.MockRouter,
) -> None:
    """If the actual streamed bytes exceed _MAX_IMAGE_BYTES (no usable
    Content-Length), raise ProviderError mid-stream."""
    # Build a body larger than the cap so iter_bytes accumulates past it.
    big_body = b"\x00" * (bing._MAX_IMAGE_BYTES + 1024)
    respx_mock.get(bing._METADATA_URL).mock(
        return_value=httpx.Response(
            200,
            json={"images": [{"url": "/th?id=huge.jpg"}]},
        )
    )
    respx_mock.get("https://www.bing.com/th?id=huge.jpg").mock(
        return_value=httpx.Response(
            200,
            content=big_body,
            headers={"content-type": "image/jpeg"},
        )
    )
    with pytest.raises(ProviderError, match="download cap"):
        bing.fetch({})


# --- Phase 1: provider-declared option schemas ---


def test_builtin_providers_declare_option_schemas() -> None:
    """Every built-in provider must implement trinity_provider_options_schema
    and return a pydantic BaseModel with extra='forbid'."""
    from pydantic import BaseModel

    from trinity.providers import get_provider_options_schema, make_plugin_manager

    pm = make_plugin_manager()
    for name in ("bing", "file", "solid"):
        schema = get_provider_options_schema(pm, name)
        assert schema is not None, f"{name} has no options schema"
        assert issubclass(schema, BaseModel)
        # All built-in schemas use extra='forbid' to reject unknown keys.
        assert schema.model_config.get("extra") == "forbid", (
            f"{name} schema must forbid extra keys"
        )


def test_provider_schema_rejects_unknown_keys() -> None:
    """An unknown key in the schema raises ValidationError, not a silent
    pass-through."""
    from pydantic import ValidationError

    from trinity.providers.builtin.bing import BingOptions
    from trinity.providers.builtin.file import FileOptions
    from trinity.providers.builtin.solid import SolidOptions

    with pytest.raises(ValidationError):
        BingOptions.model_validate({"mkt": "en-US", "resoultion": "1920x1080"})
    with pytest.raises(ValidationError):
        FileOptions.model_validate({"path": "/tmp/x.png", "extraneous": True})
    with pytest.raises(ValidationError):
        SolidOptions.model_validate({"color": "#000000", "qality": 85})


def test_load_config_validates_provider_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config() rejects an unknown provider option with a clear error
    naming the config file and the offending field."""
    from trinity.config import load_config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_path = tmp_path / "trinity" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        "[surface.source]\n"
        'provider = "bing"\n'
        "[surface.source.options]\n"
        'mkt = "en-US"\n'
        'resoultion = "1920x1080"  # typo: should be "resolution"\n'
    )
    with pytest.raises(ValueError, match=r"resoultion|bing.*rejected"):
        load_config(cfg_path)


def test_load_config_accepts_valid_provider_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config with all valid provider options loads cleanly."""
    from trinity.config import load_config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_path = tmp_path / "trinity" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        "[surface.source]\n"
        'provider = "bing"\n'
        "[surface.source.options]\n"
        'mkt = "en-GB"\n'
        'resolution = "1920x1080"\n'
        "index = 0\n"
    )
    cfg = load_config(cfg_path)
    assert cfg.surface.source.provider == "bing"


def test_validate_provider_options_fallback_for_third_party() -> None:
    """A provider without a schema hook falls back to permissive behavior
    with a logged warning."""
    import pluggy

    from trinity.providers import (
        FetchedImage,
        ProviderHooks,
        ProviderInfo,
        validate_provider_options,
    )
    from trinity.schema import Source

    pm = pluggy.PluginManager("trinity")
    pm.add_hookspecs(ProviderHooks)

    class _ThirdPartyPlugin:
        def trinity_provider_name(self) -> str:
            return "third-party"

        def trinity_provider_info(self) -> ProviderInfo:
            return ProviderInfo(
                name="third-party", description="no schema", builtin=False
            )

        def trinity_provider_fetch(self, options):
            return FetchedImage(
                data=b"\x89PNG\r\n\x1a\n" + b"x" * 16,
                content_type="image/png",
                suggested_extension=".png",
            )

    pm.register(_ThirdPartyPlugin(), name="third-party")
    source = Source(provider="third-party", options={"any_key": "any_value"})
    result = validate_provider_options(pm, source)
    # No schema → returns None (permissive fallback).
    assert result is None


def test_provider_info_renders_option_table() -> None:
    """`trinity provider info <name>` shows the option schema as a table."""
    from click.testing import CliRunner

    from trinity.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["provider", "info", "bing"])
    assert result.exit_code == 0
    assert "options:" in result.output
    for field_name in ("mkt", "resolution", "index", "timeout"):
        assert field_name in result.output


def test_schema_accepted_options_accepted_by_fetch() -> None:
    """Any options dict that passes the provider's schema is accepted by
    the registry's validate_provider_options (network and runtime errors
    are mocked)."""
    from trinity.providers import make_plugin_manager, validate_provider_options
    from trinity.providers.builtin.bing import BingOptions
    from trinity.schema import Source

    valid_dicts: list[dict[str, object]] = [
        {},
        {"mkt": "en-GB"},
        {"resolution": "1024x768"},
        {"index": 5},
        {"timeout": 60.0},
        {"mkt": "ja-JP", "resolution": "3840x2160", "index": 1, "timeout": 45.0},
    ]
    pm = make_plugin_manager()
    for d in valid_dicts:
        validated = BingOptions.model_validate(d)
        source = Source(provider="bing", options=validated.model_dump())
        result = validate_provider_options(pm, source)
        assert result is not None
        assert result == validated.model_dump()


# --- json-api ---------------------------------------------------------


def test_json_api_fetches_metadata_then_image(
    respx_mock: respx.router.MockRouter,
) -> None:
    """The json-api provider resolves a JSON pointer, follows the
    absolute image URL, and returns the image bytes."""
    from trinity.providers.builtin import json_api

    image_bytes = b"\xff\xd8\xff" + b"jsonapi-image"

    respx_mock.get("https://example.com/potd.json").mock(
        return_value=httpx.Response(
            200, json={"image": {"url": "https://example.com/wp.jpg"}}
        )
    )
    respx_mock.get("https://example.com/wp.jpg").mock(
        return_value=httpx.Response(
            200, content=image_bytes, headers={"content-type": "image/jpeg"}
        )
    )

    img = json_api.fetch(
        {
            "metadata_url": "https://example.com/potd.json",
            "image_url_pointer": "/image/url",
        }
    )
    assert img.data == image_bytes
    assert img.content_type == "image/jpeg"
    assert img.suggested_extension == ".jpg"


def test_json_api_resolves_relative_image_url(
    respx_mock: respx.router.MockRouter,
) -> None:
    """A relative image URL in the metadata is resolved against the
    metadata URL, not the localhost."""
    from trinity.providers.builtin import json_api

    image_bytes = b"\x89PNG\r\n\x1a\n" + b"png-bytes"

    respx_mock.get("https://example.com/api/potd.json").mock(
        return_value=httpx.Response(200, json={"image": {"url": "/media/wp.png"}})
    )
    respx_mock.get("https://example.com/media/wp.png").mock(
        return_value=httpx.Response(
            200, content=image_bytes, headers={"content-type": "image/png"}
        )
    )

    img = json_api.fetch(
        {
            "metadata_url": "https://example.com/api/potd.json",
            "image_url_pointer": "/image/url",
        }
    )
    assert img.data == image_bytes
    assert img.content_type == "image/png"
    assert img.suggested_extension == ".png"


def test_json_api_unescapes_pointer_tokens() -> None:
    """RFC 6901: '~0' and '~1' are unescaped after splitting on '/'."""
    from trinity.providers.builtin import _http

    doc = {"a/b": {"c~d": "value"}}
    assert _http.resolve_pointer(doc, "/a~1b/c~0d") == "value"


def test_json_api_pointer_root_returns_doc_itself() -> None:
    """The empty string pointer refers to the entire document."""
    from trinity.providers.builtin import _http

    doc = {"k": "v"}
    assert _http.resolve_pointer(doc, "") is doc


def test_json_api_pointer_array_index(
    respx_mock: respx.router.MockRouter,
) -> None:
    """Numeric tokens in a pointer index into JSON arrays."""
    from trinity.providers.builtin import json_api

    respx_mock.get("https://example.com/potd.json").mock(
        return_value=httpx.Response(
            200, json={"media": [{"url": "https://example.com/x.jpg"}]}
        )
    )
    respx_mock.get("https://example.com/x.jpg").mock(
        return_value=httpx.Response(
            200, content=b"\xff\xd8\xff" + b"x", headers={"content-type": "image/jpeg"}
        )
    )
    img = json_api.fetch(
        {
            "metadata_url": "https://example.com/potd.json",
            "image_url_pointer": "/media/0/url",
        }
    )
    assert img.data == b"\xff\xd8\xff" + b"x"


def test_json_api_rejects_non_string_pointer_target(
    respx_mock: respx.router.MockRouter,
) -> None:
    """If the pointer resolves to a number/object/array, raise
    ProviderError — only string targets are image URLs."""
    from trinity.providers.builtin import json_api

    respx_mock.get("https://example.com/potd.json").mock(
        return_value=httpx.Response(200, json={"image": 42})
    )
    with pytest.raises(ProviderError, match="non-string"):
        json_api.fetch(
            {
                "metadata_url": "https://example.com/potd.json",
                "image_url_pointer": "/image",
            }
        )


def test_json_api_rejects_http_metadata_url() -> None:
    """The JsonApiOptions schema enforces https (AnyHttpUrl) at config
    load time."""
    from pydantic import ValidationError

    from trinity.providers.builtin.json_api import JsonApiOptions

    # http:// fails AnyHttpUrl validation outright.
    with pytest.raises(ValidationError):
        JsonApiOptions(
            metadata_url="http://example.com/potd.json",  # type: ignore[arg-type]
            image_url_pointer="/url",
        )


def test_json_api_rejects_private_ip_metadata(
    respx_mock: respx.router.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the metadata hostname resolves to a private/loopback address,
    the SSRF defense raises — even with a fully-formed HTTPS URL."""
    from trinity.providers.builtin import _http, json_api

    def _raise(host: str) -> str:
        raise _http.SSRFError(
            f"host {host!r} resolves only to private/reserved addresses"
        )

    # Override the production hook (also overridden in conftest) so the
    # test resolver behaves like production and rejects any address.
    monkeypatch.setattr(_http, "_resolve_safely_hook", _raise)
    with pytest.raises(ProviderError, match=r"private|reserved"):
        json_api.fetch(
            {
                "metadata_url": "https://example.com/potd.json",
                "image_url_pointer": "/url",
            }
        )


def test_json_api_rejects_redirect_cap(
    respx_mock: respx.router.MockRouter,
) -> None:
    """A redirect chain longer than the cap is rejected."""
    from trinity.providers.builtin import json_api

    # 5 hops, each returning 302 to a fresh /hop path; respx matches
    # by path, so register N routes.  Cap is 5, so the 6th hop triggers.
    for i in range(10):
        respx_mock.get(f"https://example.com/hop{i}").mock(
            return_value=httpx.Response(
                302, headers={"location": f"https://example.com/hop{i + 1}"}
            )
        )
    with pytest.raises(ProviderError, match="redirect cap"):
        json_api.fetch(
            {
                "metadata_url": "https://example.com/hop0",
                "image_url_pointer": "/url",
            }
        )


def test_json_api_rejects_oversize_metadata(
    respx_mock: respx.router.MockRouter,
) -> None:
    """Metadata over the 5 MiB cap is rejected before parsing."""
    from trinity.providers.builtin import _http, json_api

    respx_mock.get("https://example.com/potd.json").mock(
        return_value=httpx.Response(
            200,
            content=b'{"image":{"url":"x"}}' + b" " * (_http._MAX_METADATA_BYTES + 1),
            headers={"content-type": "application/json"},
        )
    )
    with pytest.raises(ProviderError, match="metadata"):
        json_api.fetch(
            {
                "metadata_url": "https://example.com/potd.json",
                "image_url_pointer": "/image/url",
            }
        )


def test_json_api_rejects_oversize_image(
    respx_mock: respx.router.MockRouter,
) -> None:
    """An image with Content-Length over the cap is rejected pre-body."""
    from trinity.providers.builtin import _http, json_api

    respx_mock.get("https://example.com/potd.json").mock(
        return_value=httpx.Response(
            200, json={"image": {"url": "https://example.com/wp.jpg"}}
        )
    )
    respx_mock.get("https://example.com/wp.jpg").mock(
        return_value=httpx.Response(
            200,
            content=b"\x00" * 1024,
            headers={
                "content-type": "image/jpeg",
                "content-length": str(_http._MAX_IMAGE_BYTES + 1),
            },
        )
    )
    with pytest.raises(ProviderError, match="download cap"):
        json_api.fetch(
            {
                "metadata_url": "https://example.com/potd.json",
                "image_url_pointer": "/image/url",
            }
        )


# --- _http SSRF helper unit tests --------------------------------------


def test_http_sanitise_headers_caps_count() -> None:
    from trinity.providers.builtin import _http

    headers = {f"h{i}": "v" for i in range(_http._MAX_HEADERS + 50)}
    out = _http._sanitise_headers(headers)
    assert len(out) == _http._MAX_HEADERS


def test_http_sanitise_headers_drops_long_values() -> None:
    from trinity.providers.builtin import _http

    long_val = "x" * (_http._MAX_HEADER_VALUE_LEN + 1)
    out = _http._sanitise_headers({"a": long_val, "b": "ok"})
    assert out == {"b": "ok"}


def test_http_sanitise_params_caps_count() -> None:
    from trinity.providers.builtin import _http

    params = {f"p{i}": "v" for i in range(_http._MAX_PARAMS + 50)}
    out = _http._sanitise_params(params)
    assert len(out) == _http._MAX_PARAMS


def test_http_is_safe_address_rejects_private() -> None:
    from trinity.providers.builtin import _http

    assert _http._is_safe_address("10.0.0.1") is False
    assert _http._is_safe_address("192.168.1.1") is False
    assert _http._is_safe_address("127.0.0.1") is False
    assert _http._is_safe_address("169.254.0.1") is False
    assert _http._is_safe_address("::1") is False
    assert _http._is_safe_address("fc00::1") is False
    assert _http._is_safe_address("not-an-ip") is False


def test_http_is_safe_address_allows_public() -> None:
    from trinity.providers.builtin import _http

    assert _http._is_safe_address("93.184.216.34") is True
    assert _http._is_safe_address("2606:2800:220:1:248:1893:25c8:1946") is True


# --- SSRF mixed-DNS regression (Phase 1.3) ------------------------------


def test_resolve_safely_rejects_mixed_private_and_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostname resolving to both a private and a public address is
    rejected — the HTTP client may connect to the private one.
    """
    from trinity.providers.builtin import _http
    from trinity.providers.builtin._http import SSRFError

    # Simulate getaddrinfo returning both 127.0.0.1 and 1.1.1.1
    original_getaddrinfo = _http.socket.getaddrinfo

    def fake_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        if host == "evil.example.com":
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    ("127.0.0.1", port),
                ),
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    ("1.1.1.1", port),
                ),
            ]
        return original_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(_http.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SSRFError, match="unsafe address"):
        _http._resolve_safely("evil.example.com")


def test_resolve_safely_accepts_all_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostname resolving only to public addresses is accepted."""
    from trinity.providers.builtin import _http

    original_getaddrinfo = _http.socket.getaddrinfo

    def fake_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        if host == "good.example.com":
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    ("1.1.1.1", port),
                ),
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    ("8.8.8.8", port),
                ),
            ]
        return original_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(_http.socket, "getaddrinfo", fake_getaddrinfo)
    result = _http._resolve_safely("good.example.com")
    assert result in ("1.1.1.1", "8.8.8.8")


def test_resolve_safely_rejects_ipv4_mapped_ipv6_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An IPv4-mapped IPv6 address that maps to a private IPv4 is rejected."""
    from trinity.providers.builtin import _http
    from trinity.providers.builtin._http import SSRFError

    original_getaddrinfo = _http.socket.getaddrinfo

    def fake_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        if host == "mapped.example.com":
            return [
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    ("::ffff:10.0.0.1", port),
                ),
            ]
        return original_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(_http.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SSRFError, match="unsafe address"):
        _http._resolve_safely("mapped.example.com")


def test_resolve_pointer_property() -> None:
    """Random JSON documents and pointers always resolve, and the
    resolved value is the one we set when we built the pointer."""
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    from trinity.providers.builtin import _http

    @given(
        st.lists(
            st.tuples(
                st.text(
                    alphabet=st.characters(
                        blacklist_categories=("Cs", "Cc"),
                        blacklist_characters="/~",
                    ),
                    min_size=1,
                    max_size=8,
                ),
                st.integers(min_value=0, max_value=10_000),
            ),
            min_size=1,
            max_size=5,
        )
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def _inner(pairs: list[tuple[str, int]]) -> None:
        # Build a doc that contains every (key, value) pair at /pairs/i/...
        # and a non-string sentinel to ensure we only return the requested
        # token.
        doc: dict[str, object] = {}
        for i, (k, v) in enumerate(pairs):
            token = f"k{i}"
            doc[token] = {"name": k, "value": v}
        if not pairs:
            return
        idx = len(pairs) - 1
        token = f"k{idx}"
        pointer = f"/{token}/value"
        result = _http.resolve_pointer(doc, pointer)
        assert result == pairs[idx][1]

    _inner()


# --- rss ---------------------------------------------------------------

_FEED_URL = "https://feeds.example.com/potd.xml"


def _rss2_feed(items: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">'
        f"<channel><title>POTD</title>{items}</channel></rss>"
    ).encode()


def test_rss_fetches_enclosure_image(respx_mock: respx.router.MockRouter) -> None:
    from trinity.providers.builtin import rss

    image_bytes = b"\xff\xd8\xff" + b"feed-image"
    feed = _rss2_feed(
        "<item><title>Day 1</title>"
        '<enclosure url="https://img.example.com/day1.jpg" type="image/jpeg"/>'
        "</item>"
    )
    respx_mock.get(_FEED_URL).mock(return_value=httpx.Response(200, content=feed))
    respx_mock.get("https://img.example.com/day1.jpg").mock(
        return_value=httpx.Response(
            200, content=image_bytes, headers={"content-type": "image/jpeg"}
        )
    )

    img = rss.fetch({"url": _FEED_URL})
    assert img.data == image_bytes
    assert img.content_type == "image/jpeg"
    assert img.suggested_extension == ".jpg"


def test_rss_index_selects_nth_item(respx_mock: respx.router.MockRouter) -> None:
    from trinity.providers.builtin import rss

    feed = _rss2_feed(
        '<item><enclosure url="https://img.example.com/new.jpg" '
        'type="image/jpeg"/></item>'
        '<item><enclosure url="https://img.example.com/old.jpg" '
        'type="image/jpeg"/></item>'
    )
    respx_mock.get(_FEED_URL).mock(return_value=httpx.Response(200, content=feed))
    old_route = respx_mock.get("https://img.example.com/old.jpg").mock(
        return_value=httpx.Response(
            200, content=b"\xff\xd8\xffold", headers={"content-type": "image/jpeg"}
        )
    )

    rss.fetch({"url": _FEED_URL, "index": 1})
    assert old_route.called


def test_rss_media_content_and_group(respx_mock: respx.router.MockRouter) -> None:
    """Media RSS <media:content> is honoured, both directly on the item
    and nested inside <media:group>."""
    from trinity.providers.builtin import rss

    feed = _rss2_feed(
        "<item><media:group>"
        '<media:content url="https://img.example.com/grouped.png" medium="image"/>'
        "</media:group></item>"
    )
    respx_mock.get(_FEED_URL).mock(return_value=httpx.Response(200, content=feed))
    route = respx_mock.get("https://img.example.com/grouped.png").mock(
        return_value=httpx.Response(
            200, content=b"\x89PNGdata", headers={"content-type": "image/png"}
        )
    )

    img = rss.fetch({"url": _FEED_URL})
    assert route.called
    assert img.suggested_extension == ".png"


def test_rss_atom_feed_link_enclosure(respx_mock: respx.router.MockRouter) -> None:
    from trinity.providers.builtin import rss

    feed = (
        b'<?xml version="1.0"?>\n'
        b'<feed xmlns="http://www.w3.org/2005/Atom"><title>POTD</title>'
        b"<entry><title>Day 1</title>"
        b'<link rel="enclosure" type="image/jpeg" '
        b'href="https://img.example.com/atom.jpg"/>'
        b"</entry></feed>"
    )
    respx_mock.get(_FEED_URL).mock(return_value=httpx.Response(200, content=feed))
    route = respx_mock.get("https://img.example.com/atom.jpg").mock(
        return_value=httpx.Response(
            200, content=b"\xff\xd8\xffatom", headers={"content-type": "image/jpeg"}
        )
    )

    rss.fetch({"url": _FEED_URL})
    assert route.called


def test_rss_relative_enclosure_resolved_against_feed_url(
    respx_mock: respx.router.MockRouter,
) -> None:
    from trinity.providers.builtin import rss

    feed = _rss2_feed(
        '<item><enclosure url="/images/rel.jpg" type="image/jpeg"/></item>'
    )
    respx_mock.get(_FEED_URL).mock(return_value=httpx.Response(200, content=feed))
    route = respx_mock.get("https://feeds.example.com/images/rel.jpg").mock(
        return_value=httpx.Response(
            200, content=b"\xff\xd8\xffrel", headers={"content-type": "image/jpeg"}
        )
    )

    rss.fetch({"url": _FEED_URL})
    assert route.called


def test_rss_item_without_image_raises(respx_mock: respx.router.MockRouter) -> None:
    """An item with only a non-image enclosure (podcast audio) is refused."""
    from trinity.providers.builtin import rss

    feed = _rss2_feed(
        '<item><enclosure url="https://cdn.example.com/ep1.mp3" type="audio/mpeg"/>'
        "<link>https://example.com/episode-1</link></item>"
    )
    respx_mock.get(_FEED_URL).mock(return_value=httpx.Response(200, content=feed))
    with pytest.raises(ProviderError, match="declares no image"):
        rss.fetch({"url": _FEED_URL})


def test_rss_index_out_of_range_raises(respx_mock: respx.router.MockRouter) -> None:
    from trinity.providers.builtin import rss

    feed = _rss2_feed(
        '<item><enclosure url="https://img.example.com/one.jpg" '
        'type="image/jpeg"/></item>'
    )
    respx_mock.get(_FEED_URL).mock(return_value=httpx.Response(200, content=feed))
    with pytest.raises(ProviderError, match="out of range"):
        rss.fetch({"url": _FEED_URL, "index": 5})


def test_rss_malformed_xml_raises(respx_mock: respx.router.MockRouter) -> None:
    from trinity.providers.builtin import rss

    respx_mock.get(_FEED_URL).mock(
        return_value=httpx.Response(200, content=b"<rss><channel>broken")
    )
    with pytest.raises(ProviderError, match="XML"):
        rss.fetch({"url": _FEED_URL})


def test_rss_entity_expansion_rejected(respx_mock: respx.router.MockRouter) -> None:
    """A billion-laughs style DOCTYPE must be rejected by defusedxml, not
    expanded in memory."""
    from trinity.providers.builtin import rss

    bomb = (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        b"<rss><channel><item><title>&lol2;</title></item></channel></rss>"
    )
    respx_mock.get(_FEED_URL).mock(return_value=httpx.Response(200, content=bomb))
    with pytest.raises(ProviderError, match="XML"):
        rss.fetch({"url": _FEED_URL})


def test_rss_rejects_plain_http_url() -> None:
    from pydantic import ValidationError

    from trinity.providers.builtin import rss

    with pytest.raises(ValidationError, match="https"):
        rss.RssOptions.model_validate({"url": "http://feeds.example.com/potd.xml"})


def test_rss_unsupported_root_raises(respx_mock: respx.router.MockRouter) -> None:
    from trinity.providers.builtin import rss

    respx_mock.get(_FEED_URL).mock(
        return_value=httpx.Response(
            200, content=b"<html><body>not a feed</body></html>"
        )
    )
    with pytest.raises(ProviderError, match="unsupported feed root"):
        rss.fetch({"url": _FEED_URL})


# --- unknown provider crash regression (Phase 1.2) ---------------------


def test_validate_provider_options_unknown_provider_raises_valueerror() -> None:
    """An unknown provider name raises ValueError (not KeyError) with a
    clear hint pointing to 'trinity provider list'."""
    from trinity.providers import validate_provider_options

    pm = make_plugin_manager()
    source = Source(provider="nonexistent", options=SourceOptions())
    with pytest.raises(ValueError, match="unknown provider"):
        validate_provider_options(pm, source)


def test_fetch_from_source_unknown_provider_raises_provider_error() -> None:
    """An unknown provider name raises ProviderError (not KeyError) with a
    clear hint pointing to 'trinity provider list'."""
    pm = make_plugin_manager()
    source = Source(provider="nonexistent", options=SourceOptions())
    with pytest.raises(ProviderError, match="unknown provider"):
        fetch_from_source(pm, source)


# --- change probes (apply --if-changed) ---------------------------------


def test_rss_probe_is_metadata_only(respx_mock: respx.router.MockRouter) -> None:
    """The probe fetches the feed but never the image; the token is the
    resolved image URL, so it changes exactly when the feed advances."""
    from trinity.providers.builtin import rss

    feed = _rss2_feed(
        '<item><enclosure url="https://img.example.com/day1.jpg" '
        'type="image/jpeg"/></item>'
    )
    respx_mock.get(_FEED_URL).mock(return_value=httpx.Response(200, content=feed))
    image_route = respx_mock.get("https://img.example.com/day1.jpg").mock(
        return_value=httpx.Response(200, content=b"\xff\xd8\xff")
    )

    token = rss.probe({"url": _FEED_URL})
    assert token == "https://img.example.com/day1.jpg"
    assert not image_route.called


def test_bing_probe_is_metadata_only(respx_mock: respx.router.MockRouter) -> None:
    metadata_route = respx_mock.get(bing._METADATA_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "images": [
                    {
                        "url": "/th?id=OHR.Foo_1920x1080.jpg&pid=hp",
                        "urlbase": "/th?id=OHR.Foo",
                        "hsh": "abc123",
                    }
                ]
            },
        )
    )
    image_route = respx_mock.get(
        "https://www.bing.com/th?id=OHR.Foo_1920x1080.jpg&pid=hp"
    ).mock(return_value=httpx.Response(200, content=b"\xff\xd8\xff"))

    token = bing.probe({"mkt": "en-US"})
    assert metadata_route.called
    assert not image_route.called
    # hsh preferred; market folded in (same day differs per market).
    assert token == "en-US:abc123"


def test_bing_probe_falls_back_to_urlbase(
    respx_mock: respx.router.MockRouter,
) -> None:
    respx_mock.get(bing._METADATA_URL).mock(
        return_value=httpx.Response(
            200, json={"images": [{"url": "/th?id=x.jpg", "urlbase": "/th?id=x"}]}
        )
    )
    assert bing.probe({}) == "en-US:/th?id=x"


def test_solid_probe_stable_until_options_change() -> None:
    assert solid.probe({"color": "#123456"}) == solid.probe({"color": "#123456"})
    assert solid.probe({"color": "#123456"}) != solid.probe({"color": "#654321"})


def test_file_probe_changes_when_file_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    monkeypatch.setenv("TRINITY_SHARED_DIR", str(tmp_path))
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"\xff\xd8\xffv1")
    os.utime(target, (1_000_000, 1_000_000))
    first = file.probe({"path": str(target)})
    assert first == file.probe({"path": str(target)})

    target.write_bytes(b"\xff\xd8\xffv2-longer")
    os.utime(target, (2_000_000, 2_000_000))
    assert file.probe({"path": str(target)}) != first


def test_file_probe_enforces_allow_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe must not be usable to stat arbitrary paths outside the
    allow-listed roots."""
    monkeypatch.setenv("TRINITY_SHARED_DIR", str(tmp_path / "elsewhere"))
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"\xff\xd8\xff")
    with pytest.raises(ProviderError, match="not under an allowed root"):
        file.probe({"path": str(outside)})


def test_probe_from_source_dispatches() -> None:
    from trinity.providers import probe_from_source

    pm = make_plugin_manager()
    source = Source(
        provider="solid",
        options=SourceOptions.model_validate({"color": "#abcdef"}),
    )
    token = probe_from_source(pm, source)
    assert isinstance(token, str) and token


def test_probe_from_source_none_for_probeless_plugin() -> None:
    """A provider without a probe hook yields None (callers fall back to
    a full fetch)."""
    from trinity.providers import (
        FetchedImage,
        ProviderInfo,
        _BuiltinPlugin,
        probe_from_source,
    )

    pm = make_plugin_manager()
    plugin = _BuiltinPlugin(
        "no-probe",
        ProviderInfo(name="no-probe", description="x", builtin=False),
        lambda options: FetchedImage(
            data=b"\xff\xd8\xff", content_type="image/jpeg", suggested_extension=".jpg"
        ),
    )
    pm.register(plugin, name="no-probe")
    source = Source(provider="no-probe", options=SourceOptions())
    assert probe_from_source(pm, source) is None
