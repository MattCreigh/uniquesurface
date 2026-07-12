"""Tests for content-addressed wallpaper filenames, stale-file pruning,
and the ``apply --if-changed`` refresh-state machinery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from trinity import refresh_state
from trinity.manifest import Manifest
from trinity.orchestrator import apply_to_surfaces
from trinity.schema import (
    Behaviour,
    Config,
    Fonts,
    Lock,
    Login,
    Source,
    SourceOptions,
    Surface,
    ThemeTokens,
)


def _make_config(tmp_path: Path, color: str = "#1d99f3") -> Config:
    """A solid-provider config: fully deterministic, no network.

    ``theme_tokens`` is explicitly disabled — the omitted-key
    auto-migration would enable it, and the QML pipeline would then
    probe real system paths from inside the test.
    """
    return Config(
        surface=Surface(
            source=Source(
                provider="solid",
                options=SourceOptions.model_validate(
                    {"color": color, "width": 16, "height": 16}
                ),
            ),
            fonts=Fonts(),
            login=Login(),
            lock=Lock(),
            behaviour=Behaviour(
                shared_dir=str(tmp_path / "shared"),
                user_dir=str(tmp_path / "user"),
            ),
            theme_tokens=ThemeTokens(enabled=False),
        )
    )


def _apply(config: Config, **kwargs: bool) -> list[str]:
    return apply_to_surfaces(config, manifest=Manifest(), backends=[], **kwargs)


# --- content-addressed filenames ----------------------------------------


def test_apply_writes_content_addressed_filenames(tmp_path: Path) -> None:
    """The filename embeds a digest of the bytes: Plasma only repaints on
    an Image= *value* change, so a new image must be a new URI."""
    plan = _apply(_make_config(tmp_path))
    assert any(line.startswith("wrote ") for line in plan)

    for directory in (tmp_path / "user", tmp_path / "shared"):
        files = list(directory.glob("last_wallpaper-*.jpg"))
        assert len(files) == 1, directory
        digest = hashlib.sha256(files[0].read_bytes()).hexdigest()
        assert files[0].name == f"last_wallpaper-{digest[:12]}.jpg"


def test_stable_alias_tracks_current_generation(tmp_path: Path) -> None:
    """`last_wallpaper.jpg` is a symlink to the current hash-named file:
    SDDM resolves the path at greeter start and usually can't have its
    theme.conf.user rewritten by the user-mode timer, so it needs one
    fixed path that always points at the newest image."""
    shared = tmp_path / "shared"

    _apply(_make_config(tmp_path, color="#111111"))
    alias = shared / "last_wallpaper.jpg"
    assert alias.is_symlink()
    first_target = alias.resolve()
    assert first_target.name.startswith("last_wallpaper-")

    _apply(_make_config(tmp_path, color="#222222"))
    assert alias.is_symlink()
    assert alias.resolve() != first_target
    assert alias.resolve().exists()


def test_stable_alias_migrates_old_fixed_name_file(tmp_path: Path) -> None:
    """A regular last_wallpaper.jpg left behind by pre-content-addressing
    versions is atomically replaced by the alias symlink."""
    shared = tmp_path / "shared"
    shared.mkdir(parents=True)
    legacy = shared / "last_wallpaper.jpg"
    legacy.write_bytes(b"\xff\xd8\xff legacy")

    _apply(_make_config(tmp_path))
    assert legacy.is_symlink()
    assert legacy.resolve().name.startswith("last_wallpaper-")


def test_new_image_gets_new_filename_and_stale_files_are_pruned(
    tmp_path: Path,
) -> None:
    """A changed image lands under a new name; older generations are
    pruned down to the current file plus its immediate predecessor
    (which SDDM may still reference if the login backend was skipped)."""
    shared = tmp_path / "shared"

    _apply(_make_config(tmp_path, color="#111111"))
    first = next(iter(shared.glob("last_wallpaper-*.jpg")))

    _apply(_make_config(tmp_path, color="#222222"))
    second = set(shared.glob("last_wallpaper-*.jpg")) - {first}
    assert first.exists()  # predecessor kept
    assert len(second) == 1

    _apply(_make_config(tmp_path, color="#333333"))
    remaining = set(shared.glob("last_wallpaper-*.jpg"))
    assert first not in remaining  # two generations back: pruned
    assert len(remaining) == 2  # current + predecessor


# --- --if-changed ---------------------------------------------------------


def test_if_changed_skips_after_convergence(tmp_path: Path) -> None:
    """Run 1 (plain) applies. Run 2 (--if-changed) sees no stored probe
    token yet, fetches, and skips on the image digest while storing the
    token. Run 3 (--if-changed) skips on the probe token alone."""
    cfg = _make_config(tmp_path)

    plan1 = _apply(cfg)
    assert any(line.startswith("wrote ") for line in plan1)

    plan2 = _apply(cfg, if_changed=True)
    assert plan2 == ["wallpaper unchanged (image digest matches); surfaces not touched"]

    plan3 = _apply(cfg, if_changed=True)
    assert plan3 == ["source unchanged (provider change token matches); nothing to do"]


def test_if_changed_applies_when_source_config_changes(tmp_path: Path) -> None:
    """A config change invalidates the persisted state; --if-changed must
    do a full apply, not a stale skip."""
    _apply(_make_config(tmp_path, color="#111111"))
    _apply(_make_config(tmp_path, color="#111111"), if_changed=True)  # converge

    plan = _apply(_make_config(tmp_path, color="#abcdef"), if_changed=True)
    assert any(line.startswith("wrote ") for line in plan)


def test_if_changed_recovers_when_wallpaper_file_deleted(tmp_path: Path) -> None:
    """If someone deletes the wallpaper file, a matching token/digest must
    not mask the loss — the file is re-applied."""
    cfg = _make_config(tmp_path)
    _apply(cfg)
    _apply(cfg, if_changed=True)  # converge (stores probe token)

    for path in (tmp_path / "shared").glob("last_wallpaper-*.jpg"):
        path.unlink()

    plan = _apply(cfg, if_changed=True)
    assert any(line.startswith("wrote ") for line in plan)
    assert list((tmp_path / "shared").glob("last_wallpaper-*.jpg"))


def test_refresh_state_file_is_written(tmp_path: Path) -> None:
    _apply(_make_config(tmp_path))
    state_path = tmp_path / "user" / refresh_state.STATE_FILENAME
    raw = json.loads(state_path.read_text())
    assert raw["schema"] == 1
    assert set(raw) == {
        "schema",
        "fingerprint",
        "probe_token",
        "image_sha256",
        "wallpaper_path",
        "applied_at",
    }
    shared_file = Path(raw["wallpaper_path"])
    assert shared_file.exists()
    assert raw["image_sha256"] == hashlib.sha256(shared_file.read_bytes()).hexdigest()


def test_corrupt_state_degrades_to_full_apply(tmp_path: Path) -> None:
    """A corrupt state file must never block the refresh (fail open):
    with no trusted comparison baseline, --if-changed does a full apply
    and rewrites valid state."""
    cfg = _make_config(tmp_path)
    _apply(cfg)
    state_path = tmp_path / "user" / refresh_state.STATE_FILENAME
    state_path.write_text("{ not json !!!")

    plan = _apply(cfg, if_changed=True)
    assert any(line.startswith("wrote ") for line in plan)
    assert json.loads(state_path.read_text())["schema"] == 1


def test_dry_run_ignores_if_changed_state(tmp_path: Path) -> None:
    """--dry-run must always show the full plan and never write state."""
    cfg = _make_config(tmp_path)
    plan = _apply(cfg, dry_run=True, if_changed=True)
    assert any("fetch from provider" in line for line in plan)
    assert not (tmp_path / "user" / refresh_state.STATE_FILENAME).exists()
