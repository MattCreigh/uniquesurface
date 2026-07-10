"""Tests for the provider registry and built-in providers."""

from __future__ import annotations

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


def test_registry_registers_three_builtins() -> None:
    pm = make_plugin_manager()
    infos = list_providers(pm)
    names = {i.name for i in infos}
    assert names == {"bing", "file", "solid"}


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
    assert {"bing", "file", "solid"} == names


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
    with pytest.raises(ProviderError):
        solid.fetch({"color": "#000000", "width": 0, "height": 0})


def test_solid_rejects_non_numeric_dimensions() -> None:
    """Non-numeric options raise ProviderError, not a bare ValueError."""
    with pytest.raises(ProviderError, match="must be integers"):
        solid.fetch({"color": "#000000", "width": "wide", "height": 8})


def test_solid_rejects_oversize_dimensions() -> None:
    with pytest.raises(ProviderError, match="cap"):
        solid.fetch({"color": "#000000", "width": 100_000, "height": 8})


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
    with pytest.raises(ProviderError, match="HTTP request failed"):
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
    with pytest.raises(ProviderError, match="must be numeric"):
        bing.fetch({"timeout": "soon"})


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
