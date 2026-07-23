"""Tests for NetworkManager dispatcher (Feature 3)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_install_network_dispatcher_script_creates_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install_network_dispatcher_script writes a valid bash script."""
    from trinity.systemd.network_dispatcher import install_network_dispatcher_script

    dest = tmp_path / "dispatcher" / "99-trinity-wake"
    result = install_network_dispatcher_script("matt", dest_path=dest)
    assert result == dest
    assert dest.is_file()
    content = dest.read_text()
    # Must filter on EVENT="up"
    assert 'EVENT="$2"' in content
    assert '[ "$EVENT" = "up" ]' in content
    # Must run trinity apply --if-changed
    assert "trinity apply --if-changed" in content
    # Must be non-blocking
    assert "&" in content
    # Must use su - to drop privileges
    assert "su - matt" in content
    # Must be a bash script
    assert content.startswith("#!/bin/bash")


def test_install_network_dispatcher_script_sets_permissions(
    tmp_path: Path,
) -> None:
    """Dispatcher script has mode 0755."""
    from trinity.systemd.network_dispatcher import install_network_dispatcher_script

    dest = tmp_path / "disp.sh"
    install_network_dispatcher_script("user", dest_path=dest)
    mode = dest.stat().st_mode & 0o777
    assert mode == 0o755


def test_uninstall_network_dispatcher_script_removes_file(
    tmp_path: Path,
) -> None:
    """uninstall_network_dispatcher_script removes the file."""
    from trinity.systemd.network_dispatcher import (
        install_network_dispatcher_script,
        uninstall_network_dispatcher_script,
    )

    dest = tmp_path / "disp.sh"
    install_network_dispatcher_script("user", dest_path=dest)
    assert dest.is_file()
    removed = uninstall_network_dispatcher_script(dest_path=dest)
    assert removed is True
    assert not dest.exists()


def test_uninstall_network_dispatcher_script_missing_file(
    tmp_path: Path,
) -> None:
    """uninstall returns False when the file doesn't exist."""
    from trinity.systemd.network_dispatcher import uninstall_network_dispatcher_script

    removed = uninstall_network_dispatcher_script(dest_path=tmp_path / "nonexistent")
    assert removed is False


def test_dispatcher_script_ignores_down_events(tmp_path: Path) -> None:
    """The script exits early for non-up events (implicit from the filter)."""
    from trinity.systemd.network_dispatcher import install_network_dispatcher_script

    dest = tmp_path / "disp.sh"
    install_network_dispatcher_script("user", dest_path=dest)
    content = dest.read_text()
    # The script should have the event filter that exits on non-up
    assert '[ "$EVENT" = "up" ] || exit 0' in content


# --- Wake timer template -----------------------------------------------


def test_render_wake_timer_has_wake_system() -> None:
    """The wake timer template includes WakeSystem=true."""
    from trinity.systemd.writer import render_wake_timer

    text = render_wake_timer()
    assert "WakeSystem=true" in text
    assert "OnCalendar=hourly" in text
    assert "Persistent=true" in text


def test_render_timer_no_wake_system() -> None:
    """The default timer template does NOT include WakeSystem."""
    from trinity.systemd.writer import render_timer

    text = render_timer()
    assert "WakeSystem" not in text
    assert "OnCalendar=hourly" in text


def test_install_with_wake_system_uses_wake_template(
    tmp_path: Path,
) -> None:
    """install(wake_system=True) writes the wake timer."""
    from trinity.systemd.writer import install

    unit_dir = tmp_path / "units"
    trinity_bin = "/usr/bin/trinity"
    _svc, tmr = install(
        unit_dir=unit_dir,
        trinity_bin=trinity_bin,
        working_dir=str(tmp_path),
        wake_system=True,
    )
    timer_text = tmr.read_text()
    assert "WakeSystem=true" in timer_text


def test_install_without_wake_system_uses_default_template(
    tmp_path: Path,
) -> None:
    """install(wake_system=False) uses the default timer template."""
    from trinity.systemd.writer import install

    unit_dir = tmp_path / "units"
    trinity_bin = "/usr/bin/trinity"
    _svc, tmr = install(
        unit_dir=unit_dir,
        trinity_bin=trinity_bin,
        working_dir=str(tmp_path),
        wake_system=False,
    )
    timer_text = tmr.read_text()
    assert "WakeSystem" not in timer_text


def test_install_network_dispatcher_invalid_username(tmp_path: Path) -> None:
    """install_network_dispatcher_script raises ValueError for invalid
    username pattern.
    """
    from trinity.systemd.network_dispatcher import install_network_dispatcher_script

    dest = tmp_path / "disp.sh"
    for bad in ("user name", "user;inject", "matt&", "1user", "-user"):
        with pytest.raises(ValueError):
            install_network_dispatcher_script(bad, dest_path=dest)
