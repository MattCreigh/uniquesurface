"""Tests for the data-driven QML patch descriptor system.

Phase 4 moved the QML anchor regexes and managed-property lists from
hard-coded Python into TOML data files under
``src/trinity/theme/descriptors/``.  These tests exercise the loader
and the version-matching selection logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trinity.theme import descriptors
from trinity.theme.descriptors import (
    PlasmaVersion,
    _all,
    _load_all,
    _reset_cache_for_tests,
    detect_plasma_version,
    load_descriptors_from_dir,
    select,
)


@pytest.fixture(autouse=True)
def _reset_module_cache() -> None:
    """Each test sees a freshly-cached descriptor set so mutations
    in one test do not leak into another."""
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


# --- packaged descriptors ---------------------------------------------


def test_packaged_descriptors_load() -> None:
    """The three packaged descriptor files all load and validate."""
    loaded = _load_all()
    names = {d.name for d in loaded}
    assert names == {
        "sddm_login",
        "plasma_lockscreen_mainblock",
        "plasma_lockscreen_ui",
    }


def test_packaged_descriptors_have_patches() -> None:
    """Each descriptor declares at least one patch."""
    for d in _all():
        assert d.patches, f"descriptor {d.name} has no patches"


def test_sddm_login_descriptor_lists_four_font_properties() -> None:
    """The sddm_login descriptor names all four managed properties."""
    login = next(d for d in _all() if d.name == "sddm_login")
    font_patches = [p for p in login.patches if p.kind == "font_property"]
    assert len(font_patches) == 1
    props = {p.name for p in font_patches[0].font_properties}
    assert props == {"fontFamily", "fontWeight", "passwordCharacter", "clockFormat"}


def test_mainblock_descriptor_uses_wake_guard() -> None:
    mainblock = next(d for d in _all() if d.name == "plasma_lockscreen_mainblock")
    kinds = {p.kind for p in mainblock.patches}
    assert kinds == {"wake_guard"}


def test_lockscreen_ui_descriptor_uses_fadeout_timer() -> None:
    ui = next(d for d in _all() if d.name == "plasma_lockscreen_ui")
    kinds = {p.kind for p in ui.patches}
    assert kinds == {"fadeout_timer"}


# --- selection ---------------------------------------------------------


def test_select_picks_matching_descriptor() -> None:
    """select() returns the descriptor whose plasma range matches."""
    plasma = PlasmaVersion("6.5.0", "config")
    chosen = select("sddm_login", plasma)
    assert chosen is not None
    assert chosen.name == "sddm_login"


def test_select_returns_none_for_unmatched_version() -> None:
    """A Plasma version outside any descriptor's range returns None."""
    plasma = PlasmaVersion("1.0.0", "config")
    assert select("sddm_login", plasma) is None


def test_select_returns_none_for_unknown_plasma() -> None:
    """An unknown Plasma version (empty string) returns None."""
    plasma = PlasmaVersion("", "unknown")
    assert select("sddm_login", plasma) is None


def test_select_picks_most_specific_when_multiple_match() -> None:
    """When two descriptors match, the more specific range wins."""
    # Write a more specific override for a temporary dir, then point
    # the descriptors module at it.
    tmp = Path("/tmp/trinity-descriptor-test")
    tmp.mkdir(exist_ok=True)
    (tmp / "sddm_login_specific.toml").write_text(
        'name = "sddm_login"\nplasma = "==6.5.0"\n'
        'description = "specific override for 6.5.0"\n\n'
        '[[patches]]\nkind = "font_property"\n'
        '[[patches.font_properties]]\nname = "fontFamily"\n',
    )
    try:
        loaded = load_descriptors_from_dir(tmp, ["sddm_login_specific.toml"])
        # Insert into the cached list.
        descriptors._cached = list(_all()) + loaded
        plasma = PlasmaVersion("6.5.0", "config")
        chosen = select("sddm_login", plasma)
        assert chosen is not None
        assert chosen.description == "specific override for 6.5.0"
        # A different 6.x version should fall back to the packaged one.
        plasma = PlasmaVersion("6.4.0", "config")
        chosen = select("sddm_login", plasma)
        assert chosen is not None
        assert chosen.description != "specific override for 6.5.0"
    finally:
        (tmp / "sddm_login_specific.toml").unlink(missing_ok=True)


def test_select_excludes_excluded_version() -> None:
    """A descriptor with the version in ``exclude`` is skipped."""
    tmp = Path("/tmp/trinity-descriptor-test")
    tmp.mkdir(exist_ok=True)
    # More specific than the packaged range so it wins on 6.3.0.
    (tmp / "sddm_login_excl.toml").write_text(
        'name = "sddm_login"\nplasma = ">=6.3,<6.6"\n'
        'exclude = ["6.5.0"]\ndescription = "narrow range, excludes 6.5.0"\n\n'
        '[[patches]]\nkind = "font_property"\n'
        '[[patches.font_properties]]\nname = "fontFamily"\n',
    )
    try:
        loaded = load_descriptors_from_dir(tmp, ["sddm_login_excl.toml"])
        descriptors._cached = list(_all()) + loaded
        plasma = PlasmaVersion("6.5.0", "config")
        # The excluded descriptor doesn't match; the packaged one does.
        chosen = select("sddm_login", plasma)
        assert chosen is not None
        assert chosen.description != "narrow range, excludes 6.5.0"
        # And a non-excluded version picks the new one.
        plasma = PlasmaVersion("6.3.0", "config")
        chosen = select("sddm_login", plasma)
        assert chosen is not None
        assert chosen.description == "narrow range, excludes 6.5.0"
    finally:
        (tmp / "sddm_login_excl.toml").unlink(missing_ok=True)


# --- loaders -----------------------------------------------------------


def test_load_descriptors_from_dir_raises_on_missing_file() -> None:
    """A bad file name raises a clear FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_descriptors_from_dir(Path("/nonexistent"), ["nope.toml"])


def test_load_descriptors_from_dir_rejects_malformed_toml(
    tmp_path: Path,
) -> None:
    """A descriptor file missing required fields raises ValidationError."""
    from pydantic import ValidationError

    bad = tmp_path / "bad.toml"
    bad.write_text('description = "missing name and patches"\n')
    with pytest.raises(ValidationError):
        load_descriptors_from_dir(tmp_path, ["bad.toml"])


def test_load_descriptors_from_dir_rejects_invalid_regex_flags(
    tmp_path: Path,
) -> None:
    """An unknown flag name is rejected (not silently ignored)."""
    from pydantic import ValidationError

    bad = tmp_path / "bad_flags.toml"
    bad.write_text(
        'name = "x"\nplasma = ">=6.0,<6.8"\n\n'
        '[[patches]]\nkind = "fadeout_timer"\n'
        'value_template = "\\\\g<1>{value}"\n'
        "[patches.anchor]\n"
        'pattern = "foo"\n'
        'flags = ["VERBOSE"]\n',  # not in the allow-list
    )
    with pytest.raises(ValidationError):
        load_descriptors_from_dir(tmp_path, ["bad_flags.toml"])


def test_load_descriptors_from_dir_rejects_unknown_kind(
    tmp_path: Path,
) -> None:
    """A patch kind outside the allow-list is rejected."""
    from pydantic import ValidationError

    bad = tmp_path / "bad_kind.toml"
    bad.write_text(
        'name = "x"\nplasma = ">=6.0,<6.8"\n\n[[patches]]\nkind = "made_up_kind"\n',
    )
    with pytest.raises(ValidationError):
        load_descriptors_from_dir(tmp_path, ["bad_kind.toml"])


# --- Plasma version detection ------------------------------------------


def test_detect_plasma_version_returns_unknown_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No $TRINITY_PLASMA_VERSION and no plasmashell on PATH → unknown."""
    monkeypatch.delenv("TRINITY_PLASMA_VERSION", raising=False)
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = detect_plasma_version()
    assert result.source == "unknown"
    assert result.version is None
    assert not result.known


def test_detect_plasma_version_honours_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """$TRINITY_PLASMA_VERSION overrides detection when parseable."""
    monkeypatch.setenv("TRINITY_PLASMA_VERSION", "6.7.0")
    result = detect_plasma_version()
    assert result.source == "config"
    assert result.version is not None
    assert str(result.version) == "6.7.0"


def test_detect_plasma_version_ignores_invalid_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unparseable $TRINITY_PLASMA_VERSION falls through to binary."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setenv("TRINITY_PLASMA_VERSION", "not.a.version")
    result = detect_plasma_version()
    assert result.source == "unknown"


def test_parse_plasma_version_output() -> None:
    """The version parser extracts the first N.N(.N) token."""
    from trinity.theme.descriptors import _parse_plasma_version_output

    assert _parse_plasma_version_output("plasmashell 6.3.4") == "6.3.4"
    assert _parse_plasma_version_output("Qt: 6.6.1, KDE Frameworks: 6.5") == "6.6.1"
    assert _parse_plasma_version_output("garbage with no version") == ""
    assert _parse_plasma_version_output("6.5 in isolation") == "6.5"
    assert _parse_plasma_version_output("") == ""
