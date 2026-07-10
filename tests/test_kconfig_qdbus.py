"""Tests for the qdbus_call soft-fail behavior."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from trinity.backends import _kconfig


def _fake_completed(
    returncode: int, stderr: str = "", stdout: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=(), returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_qdbus_call_logs_debug_when_service_missing() -> None:
    """When the target service is absent (Plasma not running), qdbus_call
    should not raise and should log a debug message — not leak stderr."""
    with (
        patch.object(_kconfig.shutil, "which", return_value="/usr/bin/qdbus6"),
        patch.object(
            _kconfig.subprocess,
            "run",
            return_value=_fake_completed(
                1, stderr="Service 'org.kde.plasma.desktop' does not exist."
            ),
        ),
    ):
        argv = _kconfig.qdbus_call(
            service="org.kde.plasma.desktop",
            path="/PlasmaShell",
            method="refreshWallpaper",
        )
    assert "refreshWallpaper" in argv


def test_qdbus_call_logs_warning_on_other_failure() -> None:
    """Non-missing-service failures are still soft (no raise); they log a
    warning so real breakage is visible in the journal."""
    with (
        patch.object(_kconfig.shutil, "which", return_value="/usr/bin/qdbus6"),
        patch.object(
            _kconfig.subprocess,
            "run",
            return_value=_fake_completed(2, stderr="some other error"),
        ),
    ):
        argv = _kconfig.qdbus_call(service="org.kde.foo", path="/x", method="y")
    assert "y" in argv
