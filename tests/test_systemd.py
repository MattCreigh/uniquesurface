"""Tests for the systemd unit writer."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from usurface import systemd


def test_render_service_contains_usurface_bin() -> None:
    text = systemd.render_service(
        {"usurface_bin": "/usr/local/bin/usurface", "working_dir": "/home/x"}
    )
    assert "/usr/local/bin/usurface" in text
    assert "Type=oneshot" in text
    assert "WorkingDirectory=/home/x" in text


def test_render_timer_has_daily_schedule() -> None:
    text = systemd.render_timer()
    assert "OnCalendar=daily" in text
    assert "Persistent=true" in text


def test_install_writes_units(tmp_path: Path) -> None:
    unit_dir = tmp_path / "user_systemd"
    svc, tmr = systemd.install(unit_dir=unit_dir, usurface_bin="/bin/true", working_dir="/tmp")
    assert svc.exists()
    assert tmr.exists()
    assert "/bin/true" in svc.read_text()
    assert "OnCalendar=daily" in tmr.read_text()
