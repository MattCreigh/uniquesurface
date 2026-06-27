"""Tests for the provider registry and built-in providers."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
import respx

from usurface.providers import (
    FetchedImage,
    ProviderError,
    fetch_from_source,
    get_provider,
    list_providers,
    make_plugin_manager,
)
from usurface.providers.builtin import bing, file, solid
from usurface.schema import Source


# --- registry ---------------------------------------------------------


def test_registry_registers_three_builtins() -> None:
    pm = make_plugin_manager()
    infos = list_providers(pm)
    names = {i.name for i in infos}
    assert names == {"bing", "file", "solid"}


def test_get_provider_returns_matching_plugin() -> None:
    pm = make_plugin_manager()
    plugin = get_provider(pm, "solid")
    name = pm.hook.usurface_provider_name(plugin=plugin)
    assert "solid" in name


def test_get_provider_raises_for_unknown_name() -> None:
    pm = make_plugin_manager()
    with pytest.raises(KeyError):
        get_provider(pm, "no-such-provider")


def test_fetch_from_source_dispatches_correctly() -> None:
    pm = make_plugin_manager()
    source = Source(
        provider="solid",
        options={"color": "#abcdef", "width": 32, "height": 18},
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
    with pytest.raises(ProviderError):
        solid.fetch({"color": "#000000", "width": 0, "height": 0})


# --- file --------------------------------------------------------------


def test_file_provider_reads_local(tmp_path: Path) -> None:
    target = tmp_path / "wp.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake-payload")
    img = file.fetch({"path": str(target)})
    assert img.content_type == "image/png"
    assert img.suggested_extension == ".png"


def test_file_provider_requires_path() -> None:
    with pytest.raises(ProviderError):
        file.fetch({})


def test_file_provider_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ProviderError):
        file.fetch({"path": str(tmp_path / "nope.png")})


def test_file_provider_expands_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"\xff\xd8\xff" + b"jpegs")
    img = file.fetch({"path": "~/wp.jpg"})
    assert img.content_type == "image/jpeg"


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
    ).mock(return_value=httpx.Response(200, content=image_bytes, headers={"content-type": "image/jpeg"}))

    img = bing.fetch({"mkt": "en-US", "resolution": "1920x1080"})
    assert metadata_route.called
    assert image_route.called
    assert img.data == image_bytes
    assert img.content_type == "image/jpeg"


def test_bing_replaces_resolution_placeholder(respx_mock: respx.router.MockRouter) -> None:
    image_bytes = b"\xff\xd8\xff" + b"image"

    respx_mock.get(bing._METADATA_URL).mock(
        return_value=httpx.Response(
            200,
            json={"images": [{"url": "/th?id={resolution}.jpg"}]},
        )
    )
    respx_mock.get("https://www.bing.com/th?id=3840x2160.jpg").mock(
        return_value=httpx.Response(200, content=image_bytes, headers={"content-type": "image/jpeg"})
    )

    img = bing.fetch({"resolution": "3840x2160"})
    assert img.data == image_bytes


def test_bing_rejects_unexpected_metadata(respx_mock: respx.router.MockRouter) -> None:
    respx_mock.get(bing._METADATA_URL).mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    with pytest.raises(ProviderError):
        bing.fetch({})
