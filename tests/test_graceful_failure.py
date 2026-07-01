"""Tests for graceful backend failure handling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from usurface.backends.base import BackendError
from usurface.backends.login import LoginBackend
from usurface.orchestrator import apply_to_surfaces, verify_image
from usurface.schema import (
    Behaviour,
    Config,
    Fonts,
    Lock,
    Login,
    Source,
    SourceOptions,
    Surface,
)


def _make_config(tmp_path: Path) -> Config:
    return Config(
        surface=Surface(
            source=Source(
                provider="solid",
                options=SourceOptions.model_construct(
                    color="#1d99f3", width=64, height=64
                ),
            ),
            fonts=Fonts(family="DejaVu Sans"),
            login=Login(),
            lock=Lock(),
            behaviour=Behaviour(
                shared_dir=str(tmp_path / "shared"),
                user_dir=str(tmp_path / "user"),
            ),
        )
    )


def test_login_backend_raises_backenderror_when_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the SDDM theme file is not writable, LoginBackend raises
    BackendError with a hint to re-run with sudo."""
    fake_path = tmp_path / "theme.conf"
    fake_path.write_text("background=\n")
    monkeypatch.setattr("usurface.backends.login._THEME_CONF_PATH", fake_path)
    monkeypatch.setattr("os.access", lambda *a, **k: False)
    monkeypatch.setattr("os.geteuid", lambda: 1000)

    backend = LoginBackend()
    manifest = MagicMock()
    with pytest.raises(BackendError) as exc_info:
        backend.apply(manifest, tmp_path / "wallpaper.jpg")
    assert "not writable" in str(exc_info.value)
    assert exc_info.value.hint is not None
    assert "sudo" in exc_info.value.hint


def test_apply_continues_when_backend_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If one backend raises BackendError, apply still completes the others
    and reports the failure in the plan."""
    from PIL import Image

    # Create a tiny valid PNG so the image verification step succeeds.
    img_path = tmp_path / "src.png"
    Image.new("RGB", (8, 8), (0, 128, 255)).save(img_path)

    cfg = _make_config(tmp_path)
    cfg.surface.source.options = SourceOptions.model_construct(  # type: ignore[arg-type]
        color="#1d99f3", width=8, height=8
    )

    # Stub the provider fetch to return our local image.
    from usurface.providers import FetchedImage

    def fake_fetch(*args, **kwargs):  # type: ignore[no-untyped-def]
        return FetchedImage(
            data=img_path.read_bytes(),
            content_type="image/png",
            suggested_extension=".png",
        )

    monkeypatch.setattr("usurface.orchestrator.fetch_from_source", fake_fetch)
    monkeypatch.setattr("usurface.orchestrator.fetch_wallpaper", lambda c: fake_fetch())

    # Stub desktop + lock backends to succeed; login to fail.
    from usurface.backends.desktop import DesktopBackend
    from usurface.backends.lock import LockBackend

    monkeypatch.setattr(DesktopBackend, "apply", lambda self, m, w: None)
    monkeypatch.setattr(LockBackend, "apply", lambda self, m, w: None)

    def login_fail(self, m, w):  # type: ignore[no-untyped-def]
        raise BackendError("login is unhappy", hint="try sudo")

    monkeypatch.setattr(LoginBackend, "apply", login_fail)

    manifest = MagicMock()
    plan = apply_to_surfaces(cfg, manifest=manifest)
    plan_text = "\n".join(plan)

    # The two working backends should report success.
    assert "backend 'desktop' applied" in plan_text
    assert "backend 'lock' applied" in plan_text
    # The failing backend should be reported with a hint, not crash.
    assert "backend 'login' FAILED" in plan_text
    assert "login is unhappy" in plan_text
    assert "try sudo" in plan_text


def test_verify_image_strips_metadata() -> None:
    """verify_image re-encodes the input; the output is a valid image."""
    from PIL import Image
    import io

    img = Image.new("RGB", (4, 4), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out = verify_image(buf.getvalue())
    assert out
    # Re-parse to ensure validity.
    with Image.open(io.BytesIO(out)) as parsed:
        assert parsed.size == (4, 4)
