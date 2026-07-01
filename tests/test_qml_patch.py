"""Tests for the QML patcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from usurface import paths
from usurface.manifest import Manifest
from usurface.theme import qml_patch


SAMPLE_QML = """\
import QtQuick
Item {
    property string fontFamily: \"Lato\"
    property string fontWeight: \"Normal\"
    property string passwordCharacter: \"•\"
    function format(d) { return Qt.formatDateTime(d, \"hh:mm\") }
}
"""


@pytest.fixture
def seeded_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed pristine + on-disk file with matching content."""
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "sddm_login.qml").write_text(SAMPLE_QML, encoding="utf-8")
    monkeypatch.setattr(paths, "templates_dir", lambda: templates)

    vendor = tmp_path / "vendor" / "Login.qml"
    vendor.parent.mkdir()
    vendor.write_text(SAMPLE_QML, encoding="utf-8")
    return vendor


def test_first_patch_appends_sentinels(seeded_login: Path, tmp_path: Path) -> None:
    m = Manifest(tmp_path / "manifest.jsonl")
    msg = qml_patch.apply_font_tokens(
        name="sddm_login",
        vendor_path=seeded_login,
        manifest=m,
        patch=qml_patch.FontPatch(
            family="Inter",
            weight="Normal",
            password_character="*",
            clock_format="hh:mm",
        ),
    )
    assert "wrote" in msg
    text = seeded_login.read_text(encoding="utf-8")
    assert qml_patch.SENTINEL_START in text
    assert qml_patch.SENTINEL_END in text
    assert "Inter" in text
    assert "*" in text


def test_second_patch_replaces_only_block(seeded_login: Path, tmp_path: Path) -> None:
    m = Manifest(tmp_path / "manifest.jsonl")
    p = qml_patch.FontPatch("Inter", "Normal", "*", "hh:mm")
    qml_patch.apply_font_tokens(
        name="sddm_login", vendor_path=seeded_login, manifest=m, patch=p
    )
    # Snapshot the file with sentinels in place.
    snapshot = seeded_login.read_text(encoding="utf-8")
    # Now apply a different patch.
    p2 = qml_patch.FontPatch("Inter", "Bold", "•", "HH:mm")
    msg = qml_patch.apply_font_tokens(
        name="sddm_login", vendor_path=seeded_login, manifest=m, patch=p2
    )
    assert "wrote" in msg
    # Header lines preserved, only the sentinel block changed.
    assert "Lato" in seeded_login.read_text(encoding="utf-8")  # the rest unchanged
    text = seeded_login.read_text(encoding="utf-8")
    assert "Bold" in text
    assert "HH:mm" in text
    # Snapshot prepended unchanged header.
    assert (
        snapshot.split(qml_patch.SENTINEL_START)[0]
        == text.split(qml_patch.SENTINEL_START)[0]
    )


def test_remove_sentinels_restores_pristine(seeded_login: Path, tmp_path: Path) -> None:
    m = Manifest(tmp_path / "manifest.jsonl")
    qml_patch.apply_font_tokens(
        name="sddm_login",
        vendor_path=seeded_login,
        manifest=m,
        patch=qml_patch.FontPatch("Inter", "Normal", "*", "hh:mm"),
    )
    qml_patch.remove_sentinels(name="sddm_login", vendor_path=seeded_login, manifest=m)
    text = seeded_login.read_text(encoding="utf-8")
    assert text == SAMPLE_QML


def test_no_op_when_patch_unchanged(seeded_login: Path, tmp_path: Path) -> None:
    m = Manifest(tmp_path / "manifest.jsonl")
    p = qml_patch.FontPatch("Inter", "Normal", "*", "hh:mm")
    qml_patch.apply_font_tokens(
        name="sddm_login", vendor_path=seeded_login, manifest=m, patch=p
    )
    msg = qml_patch.apply_font_tokens(
        name="sddm_login", vendor_path=seeded_login, manifest=m, patch=p
    )
    assert "no change" in msg


def test_requires_pristine_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setattr(paths, "templates_dir", lambda: templates)

    vendor = tmp_path / "Login.qml"
    vendor.write_text(SAMPLE_QML, encoding="utf-8")

    with pytest.raises(RuntimeError, match="no pristine template"):
        qml_patch.apply_font_tokens(
            name="sddm_login",
            vendor_path=vendor,
            manifest=Manifest(tmp_path / "manifest.jsonl"),
            patch=qml_patch.FontPatch("Inter", "Normal", "*", "hh:mm"),
        )
