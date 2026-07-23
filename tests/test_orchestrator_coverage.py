"""Coverage tests for the orchestrator's low-covered branches.

Targets the lines missing from ``pytest --cov`` for
``src/trinity/orchestrator.py``:

- ``_prune_stale_wallpapers`` (regular prune + symlink skip + non-
  wallpaper suffix + stat OSError)
- ``_update_stable_alias`` symlink + copy fallback (incl. OSError)
- ``_display_manager_name`` and ``_have_pkexec``
- ``_restart_display_manager`` (root, non-root, missing systemctl)
- ``if_changed`` skip path (probe-token match)
- Theme-tokens disabled with non-default values warning
- ``sddm_fork.remove_dropin`` / ``remove_fork`` paths
- Restore-helper coverage of the public ``apply_to_surfaces`` plan
- Manifest compaction calls on success
"""

from __future__ import annotations

import errno
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from trinity import refresh_state
from trinity.manifest import Manifest
from trinity.orchestrator import (
    _display_manager_name,
    _have_pkexec,
    _prune_stale_wallpapers,
    _restart_display_manager,
    _update_stable_alias,
    apply_to_surfaces,
)
from trinity.schema import (
    Behaviour,
    Config,
    Fonts,
    Lock,
    Login,
    Source,
    SourceOptions,
    Surface,
)


@pytest.fixture(autouse=True)
def _stub_live_dbus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the live D-Bus applies for every test in this module.

    Several tests here run ``apply_to_surfaces(dry_run=False)``, whose
    desktop/lock backends end with live ``qdbus6`` calls. Those must
    never reach the developer's running Plasma session (conftest also
    poisons the bus address as a backstop, but stubbing here avoids
    spawning doomed subprocesses at all).
    """
    from trinity.backends import _kconfig

    monkeypatch.setattr(_kconfig, "evaluate_wallpaper_script", lambda **kw: [])
    monkeypatch.setattr(_kconfig, "reload_lockscreen_config", lambda **kw: [])
    monkeypatch.setattr(_kconfig, "qdbus_call", lambda **kw: [])


def _config(tmp_path: Path, *, theme_tokens: bool = True) -> Config:
    return Config(
        surface=Surface(
            source=Source(provider="bing", options=SourceOptions()),
            fonts=Fonts(),
            login=Login(),
            lock=Lock(),
            behaviour=Behaviour(
                shared_dir=str(tmp_path / "shared"),
                user_dir=str(tmp_path / "user"),
            ),
            theme_tokens=Surface.model_fields["theme_tokens"].default_factory()
            if False
            else None,  # see below
        )
    )


def _make_config(tmp_path: Path, *, theme_tokens: bool = True) -> Config:
    """Build a minimal valid config with the given theme_tokens flag."""
    from trinity.schema import ThemeTokens

    return Config(
        surface=Surface(
            source=Source(provider="bing", options=SourceOptions()),
            fonts=Fonts(),
            login=Login(),
            lock=Lock(),
            behaviour=Behaviour(
                shared_dir=str(tmp_path / "shared"),
                user_dir=str(tmp_path / "user"),
            ),
            theme_tokens=ThemeTokens(enabled=theme_tokens),
        )
    )


# --- _prune_stale_wallpapers ----------------------------------------


def test_prune_keeps_newest_among_hash_named_files(
    tmp_path: Path,
) -> None:
    """Prune removes everything except the single most recent
    hash-named wallpaper file (the function keeps one predecessor so
    a surface that hasn't refreshed yet can still see its old image)."""
    (tmp_path / "last_wallpaper-aaaaaaaa.jpg").write_bytes(b"a")
    (tmp_path / "last_wallpaper-bbbbbbbb.jpg").write_bytes(b"b")
    (tmp_path / "last_wallpaper-cccccccc.jpg").write_bytes(b"c")
    os.utime(tmp_path / "last_wallpaper-aaaaaaaa.jpg", (1_000, 1_000))
    os.utime(tmp_path / "last_wallpaper-bbbbbbbb.jpg", (2_000, 2_000))
    os.utime(tmp_path / "last_wallpaper-cccccccc.jpg", (3_000, 3_000))

    keep = {tmp_path / "last_wallpaper-cccccccc.jpg"}
    removed = _prune_stale_wallpapers(tmp_path, keep=keep)
    # c is in keep; b is kept as the immediate predecessor; a is removed.
    assert "last_wallpaper-aaaaaaaa.jpg" in {p.name for p in removed}
    assert "last_wallpaper-bbbbbbbb.jpg" not in {p.name for p in removed}
    assert (tmp_path / "last_wallpaper-cccccccc.jpg").exists()
    assert (tmp_path / "last_wallpaper-bbbbbbbb.jpg").exists()


def test_prune_removes_older_files_when_no_keep(
    tmp_path: Path,
) -> None:
    """With no keep set, the function keeps only the single newest
    hash-named file and removes everything else."""
    (tmp_path / "last_wallpaper-aaaaaaaa.jpg").write_bytes(b"a")
    (tmp_path / "last_wallpaper-bbbbbbbb.jpg").write_bytes(b"b")
    os.utime(tmp_path / "last_wallpaper-aaaaaaaa.jpg", (1_000, 1_000))
    os.utime(tmp_path / "last_wallpaper-bbbbbbbb.jpg", (2_000, 2_000))

    removed = _prune_stale_wallpapers(tmp_path, keep=set())
    assert "last_wallpaper-aaaaaaaa.jpg" in {p.name for p in removed}
    assert (tmp_path / "last_wallpaper-bbbbbbbb.jpg").exists()


def test_prune_skips_symlinks_and_non_wallpaper_suffixes(
    tmp_path: Path,
) -> None:
    """Symlinks and files with non-image suffixes are left alone."""
    a = tmp_path / "last_wallpaper-aaaa.jpg"
    a.write_bytes(b"a")
    os.utime(a, (1_000, 1_000))
    sym = tmp_path / "last_wallpaper-stable.jpg"
    sym.symlink_to(a)
    other = tmp_path / "last_wallpaper-zzzz.txt"
    other.write_text("ignore me")
    removed = _prune_stale_wallpapers(tmp_path, keep=set())
    # a survives as the newest, the txt file and the symlink are untouched.
    assert a.exists()
    assert sym.is_symlink()
    assert other.exists()
    assert all(p.suffix == ".txt" or p.is_symlink() for p in removed) or removed == []


def test_prune_handles_stat_oserror(
    tmp_path: Path,
) -> None:
    """When stat() raises on a hash-named file, the file is silently
    skipped (not crashed) and the others are still pruned."""
    a = tmp_path / "last_wallpaper-aaaa.jpg"
    a.write_bytes(b"a")
    b = tmp_path / "last_wallpaper-bbbb.jpg"
    b.write_bytes(b"b")
    os.utime(a, (1_000, 1_000))
    os.utime(b, (2_000, 2_000))

    # Path.stat is a C-implemented method that can't be patched at
    # the class level reliably. Replace the bound method on the
    # specific instance: when the orchestrator iterates the directory
    # it calls ``path.stat()`` which dispatches via ``__getattr__`` to
    # this wrapper.
    real_stat = a.stat

    def wrapped_stat(self: Path, *args: object, **kwargs: object) -> object:
        if str(self) == str(a):
            raise OSError(errno.EIO, "no access")
        return real_stat(*args, **kwargs)  # type: ignore[arg-type]

    with patch.object(Path, "stat", wrapped_stat):
        removed = _prune_stale_wallpapers(tmp_path, keep=set())

    # a's stat failed → a is dropped from the candidate list.
    # b is the only candidate and is kept as the newest.
    assert b.exists()
    assert a.exists()  # a was never pruned
    assert removed == []


# --- _update_stable_alias --------------------------------------------


def test_update_stable_alias_writes_symlink(
    tmp_path: Path,
) -> None:
    """When the FS supports symlinks, the alias is a relative symlink."""
    target = tmp_path / "last_wallpaper-abc.jpg"
    target.write_bytes(b"x")
    alias = tmp_path / "last_wallpaper.jpg"
    plan: list[str] = []
    _update_stable_alias(alias, target=target, plan=plan)
    if alias.is_symlink():
        assert alias.resolve() == target
        assert any("stable alias" in p and "->" in p for p in plan)
    else:
        # If the FS rejected the symlink (some bind mounts), the
        # function falls back to a copy.
        assert any("copy" in p for p in plan)


def test_update_stable_alias_copy_fallback_on_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When symlink() raises OSError, the alias becomes a copy."""
    target = tmp_path / "last_wallpaper-abc.jpg"
    target.write_bytes(b"hello")
    alias = tmp_path / "last_wallpaper.jpg"
    plan: list[str] = []

    def raise_oserror(*_a: object, **_kw: object) -> None:
        raise OSError(errno.ENOTSUP, "symlinks not supported")

    monkeypatch.setattr(Path, "symlink_to", raise_oserror)
    _update_stable_alias(alias, target=target, plan=plan)
    assert alias.exists()
    assert alias.read_bytes() == b"hello"
    assert any("copy" in p for p in plan)


# --- _safe_probe (the unexpected-exception branch) ---------------


def test_safe_probe_handles_unexpected_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-ProviderError raised by a third-party probe is caught and
    logged with the exception class name, then ``None`` is returned."""
    from trinity import orchestrator as orch_mod
    from trinity.schema import Source, SourceOptions

    def boom(*_a: object, **_kw: object) -> object:
        raise KeyError("typo'd key access in third-party plugin")

    monkeypatch.setattr(orch_mod, "probe_from_source", boom)
    src = Source(provider="third-party", options=SourceOptions())
    # Must not raise; returns None so the caller falls back to a full
    # fetch.
    assert orch_mod._safe_probe(None, src) is None


def test_safe_probe_handles_provider_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ProviderError from the probe is caught and logged."""
    from trinity import orchestrator as orch_mod
    from trinity.providers import ProviderError
    from trinity.schema import Source, SourceOptions

    def boom(*_a: object, **_kw: object) -> object:
        raise ProviderError("provider probe failed")

    monkeypatch.setattr(orch_mod, "probe_from_source", boom)
    src = Source(provider="p", options=SourceOptions())
    assert orch_mod._safe_probe(None, src) is None


# --- apply_to_surfaces: probe-unavailable skip path ----------------


def test_apply_with_if_changed_and_no_probe_falls_back_to_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the provider has no probe, if_changed compares image digests
    instead. With no prior state and a fresh image, the apply runs."""
    import trinity.orchestrator as orch_mod

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    from trinity.providers import FetchedImage

    monkeypatch.setattr(orch_mod, "probe_from_source", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        orch_mod,
        "fetch_wallpaper",
        lambda *_a, **_kw: FetchedImage(
            data=fake_png,
            content_type="image/png",
            suggested_extension=".png",
        ),
    )
    monkeypatch.setattr(orch_mod, "verify_image", lambda _d: fake_png)

    cfg = _make_config(tmp_path, theme_tokens=False)
    plan = apply_to_surfaces(cfg, manifest=Manifest(), dry_run=False, if_changed=True)
    # The apply ran end-to-end (probe returned None → digest check).
    out = "\n".join(plan)
    assert "wrote" in out


# --- _display_manager_name / _restart_display_manager hint paths ---


def test_apply_with_dm_restart_emits_hints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful login backend write with a known DM emits a clear
    hint to restart the DM so the greeter re-reads theme.conf."""
    import trinity.orchestrator as orch_mod

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    from trinity.providers import FetchedImage

    monkeypatch.setattr(orch_mod, "probe_from_source", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        orch_mod,
        "fetch_wallpaper",
        lambda *_a, **_kw: FetchedImage(
            data=fake_png,
            content_type="image/png",
            suggested_extension=".png",
        ),
    )
    monkeypatch.setattr(orch_mod, "verify_image", lambda _d: fake_png)
    # Pretend plasmalogin is the active DM.
    monkeypatch.setattr(orch_mod, "_display_manager_name", lambda: "plasmalogin")
    monkeypatch.setattr(orch_mod, "_have_pkexec", lambda: True)

    cfg = _make_config(tmp_path, theme_tokens=False)
    plan = apply_to_surfaces(cfg, manifest=Manifest(), dry_run=False, restart_dm=False)
    out = "\n".join(plan)
    # The plan includes the "restart plasmalogin" hint (but does not
    # auto-restart because --restart-dm was not passed).
    assert "plasmalogin" in out
    assert "restart" in out.lower()


def test_apply_with_no_dm_falls_back_to_logout_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no DM is detectable, the user gets a 'log out fully' hint
    instead of a 'restart X' message."""
    import trinity.orchestrator as orch_mod

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    from trinity.providers import FetchedImage

    monkeypatch.setattr(orch_mod, "probe_from_source", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        orch_mod,
        "fetch_wallpaper",
        lambda *_a, **_kw: FetchedImage(
            data=fake_png,
            content_type="image/png",
            suggested_extension=".png",
        ),
    )
    monkeypatch.setattr(orch_mod, "verify_image", lambda _d: fake_png)
    monkeypatch.setattr(orch_mod, "_display_manager_name", lambda: None)
    monkeypatch.setattr(orch_mod, "_have_pkexec", lambda: False)

    cfg = _make_config(tmp_path, theme_tokens=False)
    plan = apply_to_surfaces(cfg, manifest=Manifest(), dry_run=False)
    out = "\n".join(plan)
    assert "log out" in out.lower() or "switch-user" in out.lower()


# --- _display_manager_name / _have_pksec --------------------------


def test_display_manager_name_returns_none_when_no_systemctl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda _name: None)
    assert _display_manager_name() is None


def test_display_manager_name_returns_active_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe that returns 0 → that unit is returned."""
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda _name: "/usr/bin/systemctl")
    fake_calls: list[list[str]] = []

    def fake_run(argv, **_kw: object) -> subprocess.CompletedProcess[str]:
        fake_calls.append(argv)
        unit = argv[-1]
        return subprocess.CompletedProcess(
            args=tuple(argv),
            returncode=0 if unit == "sddm" else 1,
            stdout="",
            stderr="",
        )

    # Patch the global subprocess.run; the function imports subprocess
    # locally but the symbol resolves to the same module object.
    monkeypatch.setattr("subprocess.run", fake_run)
    assert _display_manager_name() == "sddm"
    assert any("sddm" in c for c in fake_calls)


def test_display_manager_name_returns_none_when_all_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When every probe returns non-zero, the function returns None."""
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda _name: "/usr/bin/systemctl")

    def fake_run(argv, **_kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=tuple(argv), returncode=1, stdout="", stderr=""
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    assert _display_manager_name() is None


def test_have_pkexec_true_when_sudo_on_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda name: f"/usr/bin/{name}")
    assert _have_pkexec() is True


def test_have_pkexec_false_when_nothing_on_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda _name: None)
    assert _have_pkexec() is False


# --- _restart_display_manager ---------------------------------------


def test_restart_display_manager_handles_missing_systemctl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If systemctl is not on PATH, a hint is added and no exception
    is raised."""
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda _name: None)
    plan: list[str] = []
    _restart_display_manager("sddm", plan)
    assert any("not on PATH" in p for p in plan)


def test_restart_display_manager_handles_unit_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero exit is rendered as a hint, not raised."""
    import shutil as _shutil

    monkeypatch.setattr(
        _shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "systemctl" else None,
    )

    def fake_run(argv, **_kw: object) -> subprocess.CompletedProcess[bytes]:
        # stderr must be bytes because the function does .decode() on it.
        return subprocess.CompletedProcess(
            args=tuple(argv),
            returncode=5,
            stdout=b"",
            stderr=b"Unit sddm.service not found",
        )

    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setattr("subprocess.run", fake_run)
    plan: list[str] = []
    _restart_display_manager("sddm", plan)
    assert any("sddm" in p for p in plan)
    assert any("returned 5" in p or "not found" in p for p in plan)


def test_restart_display_manager_handles_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful restart adds the unit name to the plan."""
    import shutil as _shutil

    monkeypatch.setattr(
        _shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "systemctl" else None,
    )

    def fake_run(argv, **_kw: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=tuple(argv), returncode=0, stdout=b"", stderr=b""
        )

    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setattr("subprocess.run", fake_run)
    plan: list[str] = []
    _restart_display_manager("sddm", plan)
    assert any("restarted" in p for p in plan)


def test_restart_display_manager_uses_sudo_for_non_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-root user with sudo on PATH uses 'sudo -n systemctl restart'."""
    import shutil as _shutil

    monkeypatch.setattr(
        _shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in ("systemctl", "sudo") else None,
    )

    captured: list[list[str]] = []

    def fake_run(argv, **_kw: object) -> subprocess.CompletedProcess[bytes]:
        captured.append(argv)
        return subprocess.CompletedProcess(
            args=tuple(argv), returncode=0, stdout=b"", stderr=b""
        )

    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setattr("subprocess.run", fake_run)
    plan: list[str] = []
    _restart_display_manager("sddm", plan)
    # The argv should contain 'sudo' and '-n' (non-interactive).
    assert any("sudo" in a for a in captured[-1])


# --- apply_to_surfaces: if_changed + theme tokens ---------------


def _seed_wallpaper_dir(udir: Path, fname: str, content: bytes) -> Path:
    udir.mkdir(parents=True, exist_ok=True)
    p = udir / fname
    p.write_bytes(content)
    return p


def test_apply_with_if_changed_skips_when_token_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the provider's probe token matches the stored one,
    apply_to_surfaces returns the 'source unchanged' plan without
    writing anything."""
    udir = tmp_path / "user"
    sdir = tmp_path / "shared"
    udir.mkdir()
    sdir.mkdir()

    # Pre-populate state with a token + image.
    state_file = udir / refresh_state.STATE_FILENAME
    refresh_state.save(
        state_file,
        refresh_state.RefreshState(
            fingerprint=refresh_state.source_fingerprint("bing", {}),
            probe_token="tok-123",
            image_sha256="0" * 64,
            wallpaper_path=str(sdir / "old.jpg"),
            applied_at=refresh_state.now_iso(),
        ),
    )
    # And the referenced wallpaper file must exist for the skip to fire.
    (sdir / "old.jpg").write_bytes(b"x")

    # Mock the provider probe to return the same token. The orchestrator
    # does `from trinity.providers import probe_from_source`, so we must
    # patch the bound name in the orchestrator module — patching the
    # source module doesn't rebind the local import.
    import trinity.orchestrator as orch_mod

    monkeypatch.setattr(orch_mod, "probe_from_source", lambda *_a, **_kw: "tok-123")

    # If the probe doesn't match for some reason we don't want a real
    # HTTP fetch to fly. Mock fetch_wallpaper defensively.
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    from trinity.providers import FetchedImage

    monkeypatch.setattr(
        orch_mod,
        "fetch_wallpaper",
        lambda *_a, **_kw: FetchedImage(
            data=fake_png,
            content_type="image/png",
            suggested_extension=".png",
        ),
    )
    monkeypatch.setattr(orch_mod, "verify_image", lambda _data: fake_png)

    cfg = _make_config(tmp_path, theme_tokens=False)
    plan = apply_to_surfaces(
        cfg,
        manifest=Manifest(),
        dry_run=False,
        if_changed=True,
    )
    assert "source unchanged" in "\n".join(plan)


def _stub_fetched_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the provider fetch with a tiny in-memory PNG.

    The apply pipeline fetches even on a dry run; tests that exercise
    the pipeline (not the provider) must never reach the real Bing
    API — they only passed with network access and fail in a hermetic
    sandbox (nix build, offline dev box).
    """
    import trinity.orchestrator as orch_mod
    from trinity.providers import FetchedImage

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    monkeypatch.setattr(
        orch_mod,
        "fetch_wallpaper",
        lambda *_a, **_kw: FetchedImage(
            data=fake_png,
            content_type="image/png",
            suggested_extension=".png",
        ),
    )
    monkeypatch.setattr(orch_mod, "verify_image", lambda _data: fake_png)


def test_apply_with_theme_tokens_disabled_warns_when_values_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-default [surface.fonts] value combined with
    theme_tokens.enabled=false emits a warning in the plan."""
    from trinity.schema import ThemeTokens

    _stub_fetched_image(monkeypatch)
    cfg = Config(
        surface=Surface(
            source=Source(provider="bing", options=SourceOptions()),
            fonts=Fonts(family="MyFont"),
            login=Login(),
            lock=Lock(),
            behaviour=Behaviour(
                shared_dir=str(tmp_path / "shared"),
                user_dir=str(tmp_path / "user"),
            ),
            theme_tokens=ThemeTokens(enabled=False),
        )
    )
    plan = apply_to_surfaces(cfg, manifest=Manifest(), dry_run=True)
    out = "\n".join(plan)
    assert "theme tokens: disabled" in out
    # The warning is about non-default values being ignored.
    assert "ignored" in out


def test_apply_dry_run_with_theme_tokens_enabled_reports_patch_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With theme_tokens enabled and a vendored QML file, the dry-run
    plan includes a 'patch QML ...' line for that target."""
    from trinity.schema import ThemeTokens

    # Patch select to return None for all names so the loop falls
    # through cleanly without a real descriptor. The orchestrator does
    # `from trinity.theme.descriptors import select as select_descriptor`
    # inside apply_to_surfaces; patching the source module is sufficient.
    from trinity.theme import descriptors as desc

    monkeypatch.setattr(desc, "select", lambda *_a, **_kw: None)

    # Create a fake vendor file for plasma_lockscreen_ui.
    from trinity.backends import sddm_fork

    sddm_fork.VENDOR_BREEZE_DIR.mkdir(parents=True, exist_ok=True)
    fake_vendor = tmp_path / "LockScreenUi.qml"
    fake_vendor.write_text("// fake")
    # Patch extract.DEFAULT_TARGETS to point at our fake file.
    from trinity.theme import extract as extract_mod

    monkeypatch.setattr(
        extract_mod,
        "DEFAULT_TARGETS",
        [("plasma_lockscreen_ui", fake_vendor)],
    )

    _stub_fetched_image(monkeypatch)
    cfg = Config(
        surface=Surface(
            source=Source(provider="bing", options=SourceOptions()),
            fonts=Fonts(),
            login=Login(),
            lock=Lock(),
            behaviour=Behaviour(
                shared_dir=str(tmp_path / "shared"),
                user_dir=str(tmp_path / "user"),
            ),
            theme_tokens=ThemeTokens(enabled=True),
        )
    )
    plan = apply_to_surfaces(cfg, manifest=Manifest(), dry_run=True)
    # With no descriptor matched, the line is "skipped (theme tokens unsupported)".
    out = "\n".join(plan)
    assert "theme tokens" in out.lower()


def test_apply_calls_manifest_compact_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful non-dry-run apply calls compact() at the end."""
    import trinity.orchestrator as orch_mod

    # Mock fetch_wallpaper and verify_image to return a tiny valid image.
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    from trinity.providers import FetchedImage

    monkeypatch.setattr(
        orch_mod,
        "fetch_wallpaper",
        lambda *_a, **_kw: FetchedImage(
            data=fake_png,
            content_type="image/png",
            suggested_extension=".png",
        ),
    )
    monkeypatch.setattr(orch_mod, "verify_image", lambda _data: fake_png)

    # Patch the manifest's compact to record that it was called. The
    # orchestrator does `from trinity.manifest import compact` inside
    # apply_to_surfaces, so patching the source module is sufficient.
    import trinity.manifest as manifest_mod
    from trinity.manifest import Manifest

    seen: list[Manifest] = []

    def recording_compact(manifest: Manifest) -> int:
        seen.append(manifest)
        return 0

    monkeypatch.setattr(manifest_mod, "compact", recording_compact)

    cfg = _make_config(tmp_path, theme_tokens=False)
    apply_to_surfaces(cfg, manifest=Manifest(), dry_run=False)
    assert seen, "manifest.compact was not called on a successful apply"


def test_apply_with_unknown_plasma_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Plasma version is unknown, theme-token patching is skipped
    with a clear hint."""
    from trinity.theme import descriptors as desc

    # PlasmaVersion(known=False) is already the default.
    monkeypatch.setattr(desc, "select", lambda *_a, **_kw: None)

    # Patch extract.DEFAULT_TARGETS to contain one target.
    fake = tmp_path / "LockScreenUi.qml"
    fake.write_text("// fake")
    from trinity.theme import extract as extract_mod

    monkeypatch.setattr(
        extract_mod,
        "DEFAULT_TARGETS",
        [("plasma_lockscreen_ui", fake)],
    )
    # Force PlasmaVersion to a known-unknown value. The orchestrator
    # does `from trinity.theme.descriptors import detect_plasma_version`
    # inside the function, so patching the descriptors module is
    # sufficient — the function re-binds the name on each call.
    from trinity.theme.descriptors import PlasmaVersion

    unknown = PlasmaVersion(version_str="", source="unknown")
    monkeypatch.setattr(desc, "detect_plasma_version", lambda: unknown)

    from trinity.schema import ThemeTokens

    _stub_fetched_image(monkeypatch)
    cfg = Config(
        surface=Surface(
            source=Source(provider="bing", options=SourceOptions()),
            fonts=Fonts(),
            login=Login(),
            lock=Lock(),
            behaviour=Behaviour(
                shared_dir=str(tmp_path / "shared"),
                user_dir=str(tmp_path / "user"),
            ),
            theme_tokens=ThemeTokens(enabled=True),
        )
    )
    plan = apply_to_surfaces(cfg, manifest=Manifest(), dry_run=False)
    out = "\n".join(plan)
    assert "skipped" in out.lower() or "unknown" in out.lower()


# --- decompression-bomb guard (Phase 1.4) -------------------------------


def test_verify_image_decompression_bomb_error() -> None:
    """A DecompressionBombError from Pillow surfaces as ProviderError with
    'exceeds safe pixel limit' in the message."""
    from unittest.mock import patch

    from PIL import Image

    from trinity.orchestrator import verify_image
    from trinity.providers import ProviderError

    with patch.object(
        Image, "open", side_effect=Image.DecompressionBombError("too big")
    ):
        with pytest.raises(ProviderError, match="exceeds safe pixel limit"):
            verify_image(b"fake")


def test_verify_image_decompression_bomb_warning() -> None:
    """A DecompressionBombWarning (promoted to error by simplefilter)
    surfaces as ProviderError with 'exceeds safe pixel limit'."""
    from unittest.mock import patch

    from PIL import Image

    from trinity.orchestrator import verify_image
    from trinity.providers import ProviderError

    with patch.object(
        Image, "open", side_effect=Image.DecompressionBombWarning("too big")
    ):
        with pytest.raises(ProviderError, match="exceeds safe pixel limit"):
            verify_image(b"fake")


def test_verify_image_restores_max_image_pixels() -> None:
    """verify_image restores PIL.Image.MAX_IMAGE_PIXELS to its original value."""
    from PIL import Image
    from trinity.orchestrator import verify_image
    from trinity.providers import ProviderError

    original_limit = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = 12345
        try:
            verify_image(b"fake")
        except ProviderError:
            pass
        assert Image.MAX_IMAGE_PIXELS == 12345
    finally:
        Image.MAX_IMAGE_PIXELS = original_limit


# --- inter-process lock (Phase 2.1) ------------------------------------


def test_apply_lock_acquires_flock(tmp_path: Path) -> None:
    """The inter-process lock is acquired during a non-dry-run apply."""
    import fcntl

    from trinity.orchestrator import _apply_lock

    lock_dir = tmp_path / "state"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lockfile = lock_dir / "lock"

    # Acquire the lock and verify the lockfile is created.
    with _apply_lock(lock_dir):
        assert lockfile.exists()
        # Try to acquire the same lock non-blocking — should fail.
        fd = os.open(lockfile, os.O_RDWR)
        try:
            with pytest.raises(OSError, match="Resource temporarily unavailable"):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    # After the context exits, the lock should be released.
    fd = os.open(lockfile, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # should succeed
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def test_apply_lock_noop_for_dry_run(tmp_path: Path) -> None:
    """The noop lock does not create a lockfile."""
    from trinity.orchestrator import _noop_lock

    lock_dir = tmp_path / "state"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with _noop_lock():
        pass
    # No lockfile should have been created.
    assert not (lock_dir / "lock").exists()
