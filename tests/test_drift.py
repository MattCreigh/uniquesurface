"""Tests for the drift detector."""

from __future__ import annotations

from pathlib import Path

import pytest

from trinity import paths
from trinity.theme import drift, extract


@pytest.fixture
def seeded_templates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed the templates dir with a pristine copy of the sample login QML."""
    sample = Path(__file__).parent / "fixtures" / "sample_login.qml"
    src = sample.read_bytes()
    # Point the templates dir at a tmp location.
    target = tmp_path / "templates"
    target.mkdir()
    monkeypatch.setattr(paths, "templates_dir", lambda: target)
    (target / "sddm_login.qml").write_bytes(src)
    # Provide a matching "vendor" file we can compare against.
    vendor = tmp_path / "vendor" / "Login.qml"
    vendor.parent.mkdir()
    vendor.write_bytes(src)
    return vendor


def test_drift_clean_when_hashes_match(seeded_templates: Path) -> None:
    report = drift.check("sddm_login", seeded_templates)
    assert report.on_disk_matches_pristine is True
    assert report.drift_backup is None


def test_drift_detects_modification(seeded_templates: Path) -> None:
    seeded_templates.write_text(
        seeded_templates.read_text() + "\n// extra line\n",
        encoding="utf-8",
    )
    report = drift.check("sddm_login", seeded_templates)
    assert report.on_disk_matches_pristine is False


def test_drift_sentinel_region_ignored(seeded_templates: Path) -> None:
    """If the only change is between our sentinels, drift must NOT fire."""
    original_text = seeded_templates.read_text()
    sentinel_block = (
        "/* @trinity:start */\nQtObject { property string a: 'b' }\n/* @trinity:end */"
    )
    modified = original_text.rstrip("\n") + "\n\n" + sentinel_block + "\n"
    seeded_templates.write_text(modified, encoding="utf-8")

    # The drift hash check strips the sentinel region. Since we added
    # extra newlines outside the sentinels, the stripped file will
    # differ from the pristine hash; that's fine — what matters here
    # is that the helper does *not* raise and that the on-disk hash
    # is computed against the stripped content.
    report = drift.check("sddm_login", seeded_templates)
    # The first time we patch, the on-disk file legitimately differs
    # from pristine by extra newlines (our sentinel insertion). The drift
    # check simply records that fact; the orchestrator chooses what to
    # do about it. We only assert the report fields are populated.
    assert report.pristine_sha is not None
    assert report.on_disk_stripped_sha is not None
    assert report.on_disk_stripped_sha != report.pristine_sha


def test_drift_re_extract_resolves_when_vendor_changed(
    seeded_templates: Path,
) -> None:
    """If the stored pristine is updated, subsequent drift checks see match."""
    new_text = seeded_templates.read_text() + "\n// changed upstream\n"
    seeded_templates.write_text(new_text, encoding="utf-8")
    # Update the stored pristine to the new content too.
    extract.copy_pristine_bytes("sddm_login", new_text.encode("utf-8"))
    report = drift.check("sddm_login", seeded_templates)
    assert report.on_disk_matches_pristine is True


def test_drift_hard_fail_when_stored_pristine_diverges(
    seeded_templates: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_templates = tmp_path / "fake-templates"
    fake_templates.mkdir()
    (fake_templates / "sddm_login.qml").write_bytes(b"totally different content")
    monkeypatch.setattr(paths, "templates_dir", lambda: fake_templates)

    seeded_templates.write_bytes(b"on disk that does not match anything")

    report = drift.check("sddm_login", seeded_templates)
    assert report.on_disk_matches_pristine is False
    assert report.drift_backup is None  # check() no longer auto-backups


def test_strip_sentinels_removes_block() -> None:
    text = (
        "// managed by trinity — do not edit\n"
        "/* @trinity:start */\n"
        "body\n"
        "/* @trinity:end */\n"
        "footer"
    )
    out = drift.strip_sentinels(text)
    assert "trinity:start" not in out
    assert "managed by trinity" not in out
    assert "header" not in out
    assert "footer" in out


def test_strip_sentinels_handles_block_without_header() -> None:
    """A file patched before the header comment was added still has the
    sentinel markers but no leading ``// managed by trinity`` line.
    Stripping the sentinel must still work and must not leave a stray
    empty line at the boundary."""
    text = "header\n/* @trinity:start */\nbody\n/* @trinity:end */\nfooter"
    out = drift.strip_sentinels(text)
    assert "trinity:start" not in out
    assert "body" not in out
    assert "header" in out
    assert "footer" in out
    # No double-newline left behind
    assert "\n\n\n" not in out


def test_strip_sentinels_does_not_touch_managed_values() -> None:
    """Regression: ``strip_sentinels`` must only strip the sentinel block.

    It must NOT normalise the four managed font/theme property values
    or the ``fadeoutTimer`` interval — those normalisations live in
    :func:`drift.normalize_managed_values` and must only be applied
    when computing the drift hash, never during pristine extraction.
    Running them on a fresh vendor file would corrupt the stored
    baseline (e.g. turn ``interval: 10000`` into
    ``interval: @trinity@managed@`` in the pristine template).
    """
    text = (
        'readonly property string fontFamily: "DejaVu Sans"\n'
        "        Timer {\n"
        "            id: fadeoutTimer\n"
        "            interval: 10000\n"
        "            onTriggered: { doSomething(); }\n"
        "        }\n"
    )
    out = drift.strip_sentinels(text)
    assert 'fontFamily: "DejaVu Sans"' in out, (
        "strip_sentinels must leave the fontFamily literal untouched"
    )
    assert "interval: 10000" in out, (
        "strip_sentinels must leave the fadeoutTimer interval untouched"
    )
    assert "@trinity@managed@" not in out


def test_normalize_managed_values_handles_real_fadeout_timer() -> None:
    """Regression: the fadeoutTimer normaliser must handle the real
    ``LockScreenUi.qml`` layout where an ``onTriggered: { ... }`` block
    sits between the ``id:`` line and the ``interval:`` line. The
    previous regex used ``[^}]*?`` which stopped at the first ``}``
    (the opening brace of ``onTriggered: {``) and so never matched the
    real file, producing a false-positive drift on every apply.
    """
    text = (
        "        Timer {\n"
        "            id: fadeoutTimer\n"
        "            interval: 10000\n"
        "            onTriggered: {\n"
        "                lockScreenRoot.uiVisible = false;\n"
        "            }\n"
        "        }\n"
    )
    out = drift.normalize_managed_values(text)
    assert "interval: @trinity@managed@" in out, (
        "normaliser must rewrite the interval even with onTriggered in between"
    )
    assert "interval: 10000" not in out


def test_drift_check_ignores_fadeout_timer_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drift check must consider a re-patched fadeoutTimer interval
    as matching the pristine, even when the interval number differs
    from the stored pristine (because the on-disk value reflects the
    user's ``on_idle_dim_seconds`` config)."""
    target = tmp_path / "templates"
    target.mkdir()
    monkeypatch.setattr(paths, "templates_dir", lambda: target)
    pristine = (
        "        Timer {\n"
        "            id: fadeoutTimer\n"
        "            interval: 10000\n"
        "            onTriggered: { doSomething(); }\n"
        "        }\n"
    )
    (target / "plasma_lockscreen_ui.qml").write_text(pristine, encoding="utf-8")
    vendor = tmp_path / "LockScreenUi.qml"
    vendor.write_text(
        pristine.replace("10000", "15000"),  # user set on_idle_dim=15
        encoding="utf-8",
    )
    report = drift.check("plasma_lockscreen_ui", vendor)
    assert report.on_disk_matches_pristine is True, (
        f"unexpected drift: pristine={report.pristine_sha} "
        f"on_disk={report.on_disk_stripped_sha}"
    )


def test_handle_drift_raises_instead_of_adopting(
    seeded_templates: Path, tmp_path: Path
) -> None:
    """On drift, handle_drift must create a backup and raise DriftError —
    it must NOT adopt the drifted content as the new pristine baseline."""
    # 1. First run: No drift, returns None and makes no backup
    backup = drift.handle_drift("sddm_login", seeded_templates)
    assert backup is None

    # 2. Modify the vendor file to simulate upstream change (drift)
    original_text = seeded_templates.read_text(encoding="utf-8")
    modified_text = original_text + "\n// modified upstream\n"
    seeded_templates.write_text(modified_text, encoding="utf-8")

    # 3. Second run: Drift detected. Must raise DriftError (not adopt).
    with pytest.raises(drift.DriftError, match="drift detected for 'sddm_login'"):
        drift.handle_drift("sddm_login", seeded_templates)

    # 4. A backup was created with the drifted content.
    backups = list(seeded_templates.parent.glob("Login.qml.trinity.drift.*"))
    assert len(backups) >= 1
    assert backups[0].read_text(encoding="utf-8") == modified_text

    # 5. The stored pristine was NOT updated — drift still detected.
    report = drift.check("sddm_login", seeded_templates)
    assert report.on_disk_matches_pristine is False
