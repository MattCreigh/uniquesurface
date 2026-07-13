"""Tests for the QML patcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from trinity import paths
from trinity.manifest import Manifest
from trinity.theme import qml_patch

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


def test_first_patch_replaces_property_values(
    seeded_login: Path, tmp_path: Path
) -> None:
    """The patcher rewrites the string literal of each existing
    ``property string <name>: "<old>"`` declaration in place — it must
    NOT append a ``pragma Singleton`` block (which is a syntax error in
    a non-singleton QML file and breaks kscreenlocker_greet)."""
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
    # Sentinel marker present (comment-only, valid QML).
    assert qml_patch.SENTINEL_START in text
    assert qml_patch.SENTINEL_END in text
    # The font family value was replaced in the declaration line.
    assert 'property string fontFamily: "Inter"' in text
    # passwordCharacter was replaced (was "•", now "*").
    assert 'property string passwordCharacter: "*"' in text
    # No pragma Singleton — that was the bug that broke the greeter.
    assert "pragma Singleton" not in text
    # No QtObject root appended — file stays a single-root document.
    assert "QtObject" not in text


def test_second_patch_replaces_values(seeded_login: Path, tmp_path: Path) -> None:
    m = Manifest(tmp_path / "manifest.jsonl")
    p = qml_patch.FontPatch("Inter", "Normal", "*", "hh:mm")
    qml_patch.apply_font_tokens(
        name="sddm_login", vendor_path=seeded_login, manifest=m, patch=p
    )
    # Now apply a different patch.
    p2 = qml_patch.FontPatch("Inter", "Bold", "•", "HH:mm")
    msg = qml_patch.apply_font_tokens(
        name="sddm_login", vendor_path=seeded_login, manifest=m, patch=p2
    )
    assert "wrote" in msg
    text = seeded_login.read_text(encoding="utf-8")
    assert 'property string fontWeight: "Bold"' in text
    assert 'property string passwordCharacter: "•"' in text
    # SAMPLE_QML has no clockFormat declaration, so it is not added.
    assert "clockFormat" not in text
    # The original fontFamily value "Lato" is gone (replaced by "Inter").
    assert "Lato" not in text


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
    # First patch changes fontFamily Lato->Inter and passwordCharacter •->*.
    p = qml_patch.FontPatch("Inter", "Normal", "*", "hh:mm")
    qml_patch.apply_font_tokens(
        name="sddm_login", vendor_path=seeded_login, manifest=m, patch=p
    )
    # Re-applying the same patch is a no-op (values already match).
    msg = qml_patch.apply_font_tokens(
        name="sddm_login", vendor_path=seeded_login, manifest=m, patch=p
    )
    assert "no change" in msg


def test_all_four_properties_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A QML declaring all four managed properties (like Plasma's real
    LockScreenUi.qml) has each value rewritten in place — and the result
    stays a valid single-root document with no pragma Singleton."""
    qml = (
        "import QtQuick\n"
        "Item {\n"
        '    readonly property string fontFamily: "DejaVu Sans"\n'
        '    readonly property string fontWeight: "Normal"\n'
        '    readonly property string passwordCharacter: "*"\n'
        '    readonly property string clockFormat: "hh:mm"\n'
        "}\n"
    )
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "plasma_lockscreen_ui.qml").write_text(qml, encoding="utf-8")
    monkeypatch.setattr(paths, "templates_dir", lambda: templates)

    vendor = tmp_path / "vendor" / "LockScreenUi.qml"
    vendor.parent.mkdir()
    vendor.write_text(qml, encoding="utf-8")

    m = Manifest(tmp_path / "manifest.jsonl")
    qml_patch.apply_font_tokens(
        name="plasma_lockscreen_ui",
        vendor_path=vendor,
        manifest=m,
        patch=qml_patch.FontPatch("Inter", "Bold", "•", "HH:mm"),
    )
    text = vendor.read_text(encoding="utf-8")
    assert 'readonly property string fontFamily: "Inter"' in text
    assert 'readonly property string fontWeight: "Bold"' in text
    assert 'readonly property string passwordCharacter: "•"' in text
    assert 'readonly property string clockFormat: "HH:mm"' in text
    # Critical: no pragma Singleton / extra QtObject root — that broke
    # kscreenlocker_greet and caused the blue fallback lock screen.
    assert "pragma Singleton" not in text
    assert "QtObject" not in text
    # Original value gone.
    assert "DejaVu Sans" not in text


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


def test_skip_when_no_managed_properties(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the QML declares none of the managed font/theme properties, the
    patcher must NOT append anything — appending a block to a QML file
    that doesn't expect it is a syntax error (this was the root cause of
    the blue lock screen: kscreenlocker_greet fell back to the built-in
    blue locker)."""
    qml_no_props = (
        'import QtQuick\nItem {\n    property string unrelatedThing: "x"\n}\n'
    )
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "plasma_lockscreen_ui.qml").write_text(qml_no_props, encoding="utf-8")
    monkeypatch.setattr(paths, "templates_dir", lambda: templates)

    vendor = tmp_path / "vendor" / "LockScreenUi.qml"
    vendor.parent.mkdir()
    vendor.write_text(qml_no_props, encoding="utf-8")

    m = Manifest(tmp_path / "manifest.jsonl")
    msg = qml_patch.apply_font_tokens(
        name="plasma_lockscreen_ui",
        vendor_path=vendor,
        manifest=m,
        patch=qml_patch.FontPatch("Inter", "Bold", "•", "HH:mm"),
    )
    assert "skipped" in msg
    # File unchanged — no sentinel, no pragma Singleton, no QtObject.
    assert vendor.read_text(encoding="utf-8") == qml_no_props
    assert "pragma Singleton" not in vendor.read_text(encoding="utf-8")
    assert "QtObject" not in vendor.read_text(encoding="utf-8")


# --- Appendix A3: lock token patching (fadeoutTimer interval) ---


def test_lock_patch_rewrites_fadeout_timer_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply_lock_tokens rewrites the fadeoutTimer interval seconds->ms."""
    qml = (
        "import QtQuick\n"
        "Item {\n"
        "    Timer {\n"
        "        id: fadeoutTimer\n"
        "        interval: 10000\n"
        "        onTriggered: {}\n"
        "    }\n"
        "}\n"
    )
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "plasma_lockscreen_ui.qml").write_text(qml, encoding="utf-8")
    monkeypatch.setattr(paths, "templates_dir", lambda: templates)

    vendor = tmp_path / "vendor" / "LockScreenUi.qml"
    vendor.parent.mkdir()
    vendor.write_text(qml, encoding="utf-8")

    m = Manifest(tmp_path / "manifest.jsonl")
    msg = qml_patch.apply_lock_tokens(
        name="plasma_lockscreen_ui",
        vendor_path=vendor,
        manifest=m,
        patch=qml_patch.LockPatch(on_idle_dim_seconds=30, suppress_wake_keypress=True),
    )
    assert "wrote" in msg
    text = vendor.read_text(encoding="utf-8")
    assert "interval: 30000" in text
    assert "interval: 10000" not in text
    # Sentinel marker present (comment-only).
    assert qml_patch.SENTINEL_START in text


def test_lock_patch_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-applying the same lock patch is a no-op."""
    qml = (
        "import QtQuick\nItem {\n"
        "    Timer {\n"
        "        id: fadeoutTimer\n"
        "        interval: 10000\n"
        "        onTriggered: {}\n"
        "    }\n}\n"
    )
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "plasma_lockscreen_ui.qml").write_text(qml, encoding="utf-8")
    monkeypatch.setattr(paths, "templates_dir", lambda: templates)
    vendor = tmp_path / "LockScreenUi.qml"
    vendor.parent.mkdir(parents=True, exist_ok=True)
    vendor.write_text(qml, encoding="utf-8")
    m = Manifest(tmp_path / "manifest.jsonl")
    p = qml_patch.LockPatch(on_idle_dim_seconds=20, suppress_wake_keypress=True)
    qml_patch.apply_lock_tokens(
        name="plasma_lockscreen_ui", vendor_path=vendor, manifest=m, patch=p
    )
    msg = qml_patch.apply_lock_tokens(
        name="plasma_lockscreen_ui", vendor_path=vendor, manifest=m, patch=p
    )
    assert "no change" in msg


def test_lock_patch_skips_when_no_timer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the QML has no fadeoutTimer, the lock patch skips."""
    qml = "import QtQuick\nItem {\n    property int x: 1\n}\n"
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "plasma_lockscreen_ui.qml").write_text(qml, encoding="utf-8")
    monkeypatch.setattr(paths, "templates_dir", lambda: templates)
    vendor = tmp_path / "LockScreenUi.qml"
    vendor.parent.mkdir(parents=True, exist_ok=True)
    vendor.write_text(qml, encoding="utf-8")
    m = Manifest(tmp_path / "manifest.jsonl")
    msg = qml_patch.apply_lock_tokens(
        name="plasma_lockscreen_ui",
        vendor_path=vendor,
        manifest=m,
        patch=qml_patch.LockPatch(on_idle_dim_seconds=30, suppress_wake_keypress=True),
    )
    assert "skipped" in msg
    # File unchanged.
    assert vendor.read_text(encoding="utf-8") == qml


# --- Brace-balanced wake-guard removal ---------------------------


def test_strip_wake_guard_block_handles_inner_braces() -> None:
    """If upstream reorders or wraps the inner statements, the
    brace-balanced removal still cleanly removes the entire block.

    The block below is the same as the vendor block, but with one of
    the inner statements wrapped in a brace block (e.g. a new
    ``if (x) { ... }`` guard added upstream). The line-count-coupled
    regex would have leaked two lines; the brace-balanced helper
    removes the whole ``if (...) { ... }`` and stops at the matching
    ``}``.
    """
    from trinity.theme import qml_patch

    text = (
        "Keys.onPressed: event => {\n"
        "    if (event.key === Qt.Key_Left) {\n"
        "        userSwitcher.show();\n"
        "    }\n"
        '    if (!lockScreenRoot.uiVisible && event.text !== "") '
        "{ // @trinity:suppress_wake_keypress\n"
        "        lockScreenRoot.uiVisible = true;\n"
        '        if (event.text === "a") {\n'  # extra brace block
        "            extra();\n"
        "        }\n"
        "        event.accepted = true;\n"
        "        return;\n"
        "    }\n"
        "    passwordBox.text += event.text;\n"
        "}\n"
    )
    out = qml_patch._strip_wake_guard_block(text)
    # The guard is gone.
    assert "// @trinity:suppress_wake_keypress" not in out
    # The unrelated parts of the file are intact.
    assert "passwordBox.text += event.text;" in out
    assert "userSwitcher.show();" in out


def test_strip_wake_guard_block_ignores_unrelated_braces() -> None:
    """A ``}`` in a comment inside the guard block must not terminate
    the match early — the brace walker counts at the code level, but
    a well-formed QML block has matching braces, so the count returns
    to zero at the right place."""
    from trinity.theme import qml_patch

    text = (
        'if (!lockScreenRoot.uiVisible && event.text !== "") '
        "{ // @trinity:suppress_wake_keypress\n"
        "    lockScreenRoot.uiVisible = true;\n"
        "    event.accepted = true;\n"
        "    return;\n"
        "}\n"
        "next();\n"
    )
    out = qml_patch._strip_wake_guard_block(text)
    assert "// @trinity:suppress_wake_keypress" not in out
    assert "next();" in out


def test_apply_lock_tokens_removes_wake_guard_with_extra_blank_lines() -> None:
    """A re-apply with enable=false removes the guard even when the
    file was reformatted upstream with an extra blank line."""
    from trinity.theme import qml_patch

    text = (
        "Keys.onPressed: event => {\n"
        "    if (event.key === Qt.Key_Left) {\n"
        "        userSwitcher.show();\n"
        "    }\n"
        '    if (!lockScreenRoot.uiVisible && event.text !== "") '
        "{ // @trinity:suppress_wake_keypress\n"
        "        lockScreenRoot.uiVisible = true;\n"
        "\n"  # extra blank line
        "        event.accepted = true;\n"
        "        return;\n"
        "    }\n"
        "    passwordBox.text += event.text;\n"
        "}\n"
    )
    new, present = qml_patch._apply_wake_guard(text, enable=False)
    assert "// @trinity:suppress_wake_keypress" not in new
    assert "passwordBox.text += event.text;" in new
    # The downstream reapply succeeded → handler_present is True.
    assert present is True
