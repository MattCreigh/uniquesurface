"""Tests for the login-screen theme.conf editor (Phase 5: theme.conf.user)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trinity.backends.login import LoginBackend
from trinity.manifest import Manifest


@pytest.fixture
def fake_theme_conf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the login backend at a tmp theme.conf + theme.conf.user.

    Returns the base ``theme.conf`` path.  The companion
    ``theme.conf.user`` lives in the same directory.
    """
    monkeypatch.setattr("trinity.backends.login.is_plasmalogin_active", lambda: False)
    conf = tmp_path / "breeze" / "theme.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text(
        "[General]\ntype=image\nbackground=/old/path.jpg\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("trinity.backends.login._THEME_CONF_PATH", conf)
    user_conf = conf.parent / "theme.conf.user"
    monkeypatch.setattr("trinity.backends.login._THEME_CONF_USER_PATH", user_conf)
    return conf


def test_login_writes_theme_conf_user_not_vendor(
    fake_theme_conf: Path, tmp_path: Path
) -> None:
    """Phase 5: the backend writes theme.conf.user, leaving theme.conf
    untouched so a Plasma upgrade doesn't blow away the edit."""
    backend = LoginBackend()
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"x")
    backend.apply(Manifest(tmp_path / "manifest.jsonl"), target)

    # The vendor file is unchanged.
    vendor_text = fake_theme_conf.read_text(encoding="utf-8")
    assert "/old/path.jpg" in vendor_text
    # The companion .user file has the new background.
    user_conf = fake_theme_conf.parent / "theme.conf.user"
    assert user_conf.exists()
    user_text = user_conf.read_text(encoding="utf-8")
    assert "background=" in user_text
    assert str(target.resolve()) in user_text


def test_login_replaces_stale_theme_conf_user(
    fake_theme_conf: Path, tmp_path: Path
) -> None:
    """A previous run's theme.conf.user is replaced wholesale, not
    appended to, so a stale value doesn't leak through."""
    user_conf = fake_theme_conf.parent / "theme.conf.user"
    user_conf.write_text("# stale\nbackground=/stale.jpg\n", encoding="utf-8")

    backend = LoginBackend()
    target = tmp_path / "new.jpg"
    target.write_bytes(b"x")
    backend.apply(Manifest(tmp_path / "manifest.jsonl"), target)

    text = user_conf.read_text(encoding="utf-8")
    assert "/stale.jpg" not in text
    assert str(target.resolve()) in text


def test_login_writes_accent_color_to_user_conf(
    fake_theme_conf: Path, tmp_path: Path
) -> None:
    """The accent_color lands in theme.conf.user alongside the background."""
    backend = LoginBackend(accent_color="#abcdef")
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"x")
    backend.apply(Manifest(tmp_path / "manifest.jsonl"), target)

    user_conf = fake_theme_conf.parent / "theme.conf.user"
    text = user_conf.read_text(encoding="utf-8")
    assert "color=#abcdef" in text


def test_login_dry_run_does_not_modify(fake_theme_conf: Path, tmp_path: Path) -> None:
    """The dry-run plan reports the target file but doesn't write it."""
    backend = LoginBackend()
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"x")
    plan = backend.dry_run_plan(target)
    assert any("theme.conf.user" in line for line in plan)
    user_conf = fake_theme_conf.parent / "theme.conf.user"
    assert not user_conf.exists()


def test_login_skips_when_theme_conf_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the vendor theme.conf is absent, the backend no-ops."""
    monkeypatch.setattr("trinity.backends.login.is_plasmalogin_active", lambda: False)
    missing = tmp_path / "does-not-exist.conf"
    monkeypatch.setattr("trinity.backends.login._THEME_CONF_PATH", missing)
    user_conf = tmp_path / "theme.conf.user"
    monkeypatch.setattr("trinity.backends.login._THEME_CONF_USER_PATH", user_conf)
    backend = LoginBackend()
    plan = backend.dry_run_plan(tmp_path / "wp.jpg")
    assert plan and plan[0].startswith("#")
    backend.apply(
        Manifest(tmp_path / "manifest.jsonl"), tmp_path / "wp.jpg"
    )  # must not raise


def test_login_surface_needs_root_when_user_conf_not_writable(
    fake_theme_conf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """login_surface_needs_root reports True when the .user path is
    not writable (i.e. the apply step will need sudo)."""
    from trinity.backends import login as login_mod

    # We can't actually drop privileges in a unit test, so simulate
    # by patching _can_write to return False and euid to 1000.
    monkeypatch.setattr(login_mod, "_can_write", lambda path: False)
    import os

    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    # The fixture patches _THEME_CONF_PATH to the fake; the existence
    # check on _THEME_CONF_PATH passes, so needs_root should be True.
    assert login_mod.login_surface_needs_root() is True


def test_login_surface_does_not_need_root_when_euid_zero(
    fake_theme_conf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When running as root (euid=0), needs_root reports False even if
    the file is writable-check returns False (root bypasses)."""
    import os

    from trinity.backends import login as login_mod

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert login_mod.login_surface_needs_root() is False


def test_login_surface_does_not_need_root_when_theme_conf_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the vendor theme.conf doesn't exist, needs_root returns
    False (there's nothing to write to)."""
    from trinity.backends import login as login_mod

    monkeypatch.setattr(login_mod, "is_plasmalogin_active", lambda: False)
    missing = tmp_path / "nonexistent.conf"
    monkeypatch.setattr(login_mod, "_THEME_CONF_PATH", missing)
    import os

    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert login_mod.login_surface_needs_root() is False


def test_login_apply_raises_when_not_writable(
    fake_theme_conf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-writable .user path raises BackendError with a sudo hint."""
    from trinity.backends import login as login_mod
    from trinity.backends.base import BackendError

    monkeypatch.setattr(login_mod, "_can_write", lambda path: False)
    backend = LoginBackend()
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"x")
    with pytest.raises(BackendError) as exc:
        backend.apply(Manifest(tmp_path / "manifest.jsonl"), target)
    assert "sudo" in (exc.value.hint or "")


def test_login_writes_plasmalogin_conf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With plasmalogin active, the backend writes the conf.d drop-in."""
    from trinity.backends import login as login_mod

    monkeypatch.setattr(login_mod, "is_plasmalogin_active", lambda: True)
    dropin_dir = tmp_path / "etc" / "plasmalogin.conf.d"
    dropin = dropin_dir / "trinity.conf"
    monkeypatch.setattr(login_mod, "_PLASMALOGIN_CONF_DIR", dropin_dir)
    monkeypatch.setattr(login_mod, "_PLASMALOGIN_DROPIN", dropin)

    backend = LoginBackend()
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"x")
    backend.apply(Manifest(tmp_path / "manifest.jsonl"), target)

    assert dropin.exists()
    text = dropin.read_text(encoding="utf-8")
    assert "WallpaperPluginId=org.kde.image" in text
    assert f"Image=file://{target.resolve()}" in text


def test_login_plasmalogin_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With plasmalogin active, the dry-run plan names the drop-in path."""
    from trinity.backends import login as login_mod

    monkeypatch.setattr(login_mod, "is_plasmalogin_active", lambda: True)
    dropin_dir = tmp_path / "etc" / "plasmalogin.conf.d"
    dropin = dropin_dir / "trinity.conf"
    monkeypatch.setattr(login_mod, "_PLASMALOGIN_CONF_DIR", dropin_dir)
    monkeypatch.setattr(login_mod, "_PLASMALOGIN_DROPIN", dropin)

    backend = LoginBackend()
    target = tmp_path / "wp.jpg"
    target.write_bytes(b"x")
    plan = backend.dry_run_plan(target)
    assert any("etc/plasmalogin.conf.d/trinity.conf" in line for line in plan)
    assert not dropin.exists()


def test_login_plasmalogin_needs_root_when_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With plasmalogin active, needs_root is True if the drop-in isn't writable."""
    from trinity.backends import login as login_mod

    monkeypatch.setattr(login_mod, "is_plasmalogin_active", lambda: True)
    monkeypatch.setattr(login_mod, "_can_write", lambda path: False)
    import os

    monkeypatch.setattr(os, "geteuid", lambda: 1000)

    assert login_mod.login_surface_needs_root() is True
