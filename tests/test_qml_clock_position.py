"""Tests for clock position schema and QML patching (Feature 2)."""

from __future__ import annotations

import pytest

from trinity.schema import ClockPosition, ThemeTokens

# --- ClockPosition schema ----------------------------------------------


def test_clock_position_defaults() -> None:
    """ClockPosition defaults: disabled, no alignment, no coordinates."""
    cp = ClockPosition()
    assert cp.enabled is False
    assert cp.alignment is None
    assert cp.x is None
    assert cp.y is None


def test_clock_position_alignment_validation() -> None:
    """Valid alignment tokens are accepted."""
    for align in (
        "top",
        "bottom",
        "left",
        "right",
        "center",
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
    ):
        cp = ClockPosition(enabled=True, alignment=align)
        assert cp.alignment == align


def test_clock_position_rejects_invalid_alignment() -> None:
    """Invalid alignment tokens are rejected."""
    with pytest.raises(ValueError, match="invalid clock alignment"):
        ClockPosition(enabled=True, alignment="diagonal")


def test_clock_position_rejects_negative_coords() -> None:
    """Negative x/y are rejected."""
    with pytest.raises(ValueError):
        ClockPosition(enabled=True, x=-1, y=0)
    with pytest.raises(ValueError):
        ClockPosition(enabled=True, x=0, y=-1)


def test_clock_position_accepts_coords() -> None:
    """Non-negative x/y are accepted."""
    cp = ClockPosition(enabled=True, x=100, y=200)
    assert cp.x == 100
    assert cp.y == 200


def test_theme_tokens_has_clock_position() -> None:
    """ThemeTokens includes clock_position as a field."""
    tt = ThemeTokens(enabled=True)
    assert hasattr(tt, "clock_position")
    assert isinstance(tt.clock_position, ClockPosition)


def test_theme_tokens_clock_position_in_toml() -> None:
    """clock_position can be set via TOML config."""
    from trinity.config import load_config_from_string

    config_toml = """
[surface]
schema_version = 1
[surface.source]
provider = "bing"
[surface.theme_tokens]
enabled = true
[surface.theme_tokens.clock_position]
enabled = true
alignment = "top_left"
"""
    cfg = load_config_from_string(config_toml)
    assert cfg.surface.theme_tokens.clock_position.enabled is True
    assert cfg.surface.theme_tokens.clock_position.alignment == "top_left"


# --- QML alignment mapping ---------------------------------------------


def test_alignment_to_qml_layout() -> None:
    """Alignment tokens map to QML Layout.alignment values."""
    from trinity.theme.qml_patch import _alignment_to_qml_layout

    assert _alignment_to_qml_layout("top") == "Qt.AlignTop"
    assert _alignment_to_qml_layout("bottom") == "Qt.AlignBottom"
    assert _alignment_to_qml_layout("left") == "Qt.AlignLeft"
    assert _alignment_to_qml_layout("right") == "Qt.AlignRight"
    assert _alignment_to_qml_layout("center") == "Qt.AlignHCenter | Qt.AlignVCenter"
    assert _alignment_to_qml_layout("top_left") == "Qt.AlignTop | Qt.AlignLeft"
    assert _alignment_to_qml_layout("top_right") == "Qt.AlignTop | Qt.AlignRight"
    assert _alignment_to_qml_layout("bottom_left") == "Qt.AlignBottom | Qt.AlignLeft"
    assert _alignment_to_qml_layout("bottom_right") == "Qt.AlignBottom | Qt.AlignRight"


def test_alignment_to_qml_anchors() -> None:
    """Alignment tokens map to QML anchors values."""
    from trinity.theme.qml_patch import _alignment_to_qml_anchors

    anchors = _alignment_to_qml_anchors("top")
    assert "anchors.top:" in anchors
    anchors = _alignment_to_qml_anchors("bottom_left")
    assert "anchors.bottom:" in anchors
    assert "anchors.left:" in anchors
    anchors = _alignment_to_qml_anchors("center")
    assert "anchors.centerIn:" in anchors


# --- Container detection heuristic ------------------------------------


def test_detect_layout_container_column() -> None:
    """Detect ColumnLayout preceding the clock declaration."""
    from trinity.theme.qml_patch import _detect_clock_container

    qml = """\
import QtQuick
import QtQuick.Layouts

ColumnLayout {
    Text {
        id: clock
        text: "12:00"
    }
}
"""
    container = _detect_clock_container(qml, "clock")
    assert container == "ColumnLayout"


def test_detect_layout_container_row() -> None:
    """Detect RowLayout preceding the clock declaration."""
    from trinity.theme.qml_patch import _detect_clock_container

    qml = """\
RowLayout {
    Text {
        id: clock
        text: "12:00"
    }
}
"""
    container = _detect_clock_container(qml, "clock")
    assert container == "RowLayout"


def test_detect_no_container_independent_item() -> None:
    """No layout container → independent Item (use anchors)."""
    from trinity.theme.qml_patch import _detect_clock_container

    qml = """\
import QtQuick

Item {
    Text {
        id: clock
        text: "12:00"
    }
}
"""
    container = _detect_clock_container(qml, "clock")
    assert container is None


def test_apply_clock_position_layout_alignment() -> None:
    """Clock inside a layout gets Layout.alignment."""
    from trinity.schema import ClockPosition as CP
    from trinity.theme.qml_patch import apply_clock_position_tokens

    qml = """\
import QtQuick
import QtQuick.Layouts

ColumnLayout {
    Text {
        id: clock
        text: "12:00"
    }
}
"""
    result, _msg = apply_clock_position_tokens(
        text=qml,
        clock_id="clock",
        position=CP(enabled=True, alignment="top_left"),
    )
    assert "Layout.alignment:" in result
    assert "Qt.AlignTop | Qt.AlignLeft" in result


def test_apply_clock_position_anchors_for_independent_item() -> None:
    """Clock as independent Item gets anchors."""
    from trinity.schema import ClockPosition as CP
    from trinity.theme.qml_patch import apply_clock_position_tokens

    qml = """\
import QtQuick

Item {
    Text {
        id: clock
        text: "12:00"
    }
}
"""
    result, _msg = apply_clock_position_tokens(
        text=qml,
        clock_id="clock",
        position=CP(enabled=True, alignment="bottom_right"),
    )
    assert "anchors.bottom:" in result
    assert "anchors.right:" in result


def test_apply_clock_position_preserves_visible_binding() -> None:
    """Existing visible/opacity bindings are preserved."""
    from trinity.schema import ClockPosition as CP
    from trinity.theme.qml_patch import apply_clock_position_tokens

    qml = """\
import QtQuick

Item {
    Text {
        id: clock
        text: "12:00"
        visible: someCondition
        opacity: 0.8
    }
}
"""
    result, _msg = apply_clock_position_tokens(
        text=qml,
        clock_id="clock",
        position=CP(enabled=True, alignment="top"),
    )
    assert "visible: someCondition" in result
    assert "opacity: 0.8" in result


def test_apply_clock_position_disabled_is_noop() -> None:
    """Disabled clock_position is a no-op."""
    from trinity.schema import ClockPosition as CP
    from trinity.theme.qml_patch import apply_clock_position_tokens

    qml = "import QtQuick\nItem { Text { id: clock } }"
    result, _msg = apply_clock_position_tokens(
        text=qml,
        clock_id="clock",
        position=CP(enabled=False),
    )
    assert result == qml


# --- clock position with coordinates ----------------------------------


def test_apply_clock_position_with_coordinates() -> None:
    """Explicit x/y coordinates are applied to independent items."""
    from trinity.schema import ClockPosition as CP
    from trinity.theme.qml_patch import apply_clock_position_tokens

    qml = """\
import QtQuick

Item {
    Text {
        id: clock
        text: "12:00"
    }
}
"""
    result, _msg = apply_clock_position_tokens(
        text=qml,
        clock_id="clock",
        position=CP(enabled=True, x=100, y=200),
    )
    assert "x: 100" in result
    assert "y: 200" in result


def test_apply_clock_position_clock_not_found() -> None:
    """When the clock id is not found, the text is unchanged."""
    from trinity.schema import ClockPosition as CP
    from trinity.theme.qml_patch import apply_clock_position_tokens

    qml = "import QtQuick\nItem { Text { id: otherClock } }"
    result, msg = apply_clock_position_tokens(
        text=qml,
        clock_id="clock",
        position=CP(enabled=True, alignment="top"),
    )
    assert result == qml
    assert "not found" in msg


def test_apply_clock_position_no_alignment_no_coords() -> None:
    """Enabled but no alignment or coordinates is a no-op."""
    from trinity.schema import ClockPosition as CP
    from trinity.theme.qml_patch import apply_clock_position_tokens

    qml = "import QtQuick\nItem { Text { id: clock } }"
    result, msg = apply_clock_position_tokens(
        text=qml,
        clock_id="clock",
        position=CP(enabled=True),
    )
    assert result == qml
    assert "no alignment" in msg
