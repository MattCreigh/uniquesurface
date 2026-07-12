"""Tests for the systemd unit writer."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from trinity import systemd
from trinity.systemd import writer


def _fake_process(
    stdout: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=(), returncode=returncode, stdout=stdout, stderr=""
    )


def test_render_service_contains_trinity_bin() -> None:
    text = systemd.render_service(
        {"trinity_bin": "/usr/local/bin/trinity", "home_dir": "/home/x"}
    )
    assert "/usr/local/bin/trinity" in text
    assert "Type=oneshot" in text
    assert "WorkingDirectory=/home/x" in text
    # Enterprise hardening directives must be present.
    assert "ProtectSystem=strict" in text
    assert "NoNewPrivileges=true" in text
    assert "PrivateTmp=true" in text
    assert "TimeoutStartSec=120" in text
    assert "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX" in text


def test_render_timer_has_hourly_conditional_schedule() -> None:
    text = systemd.render_timer()
    assert "OnCalendar=hourly" in text
    assert "RandomizedDelaySec=10min" in text
    assert "Persistent=true" in text


def test_render_service_uses_if_changed() -> None:
    """Hourly timer runs must be cheap: the service passes --if-changed so
    an unchanged source costs one metadata-sized request, not a download
    plus surface rewrites."""
    text = systemd.render_service({"trinity_bin": "/bin/x", "home_dir": "/home/x"})
    assert "ExecStart=/bin/x apply --if-changed" in text


def test_install_writes_units(tmp_path: Path) -> None:
    unit_dir = tmp_path / "user_systemd"
    svc, tmr = systemd.install(
        unit_dir=unit_dir, trinity_bin="/bin/true", working_dir="/tmp"
    )
    assert svc.exists()
    assert tmr.exists()
    assert "/bin/true" in svc.read_text()
    assert "OnCalendar=hourly" in tmr.read_text()


def test_install_raises_when_binary_missing(tmp_path: Path, monkeypatch) -> None:
    """A scheduled service must never point at a non-existent binary,
    which would fail with status 203/EXEC and silently stop refreshing."""
    import trinity.systemd.writer as w

    monkeypatch.setattr(w.shutil, "which", lambda _name: None)
    with pytest.raises(w.TrinityBinaryNotFound):
        systemd.install(unit_dir=tmp_path / "units")


def test_service_unit_never_passes_adopt_drift(tmp_path: Path) -> None:
    """The systemd service must NOT pass --adopt-drift: drift adoption is
    an explicit consent action, never done automatically by the timer."""
    unit_dir = tmp_path / "user_systemd"
    svc, _tmr = systemd.install(
        unit_dir=unit_dir, trinity_bin="/bin/true", working_dir="/tmp"
    )
    text = svc.read_text()
    assert "--adopt-drift" not in text
    assert "ExecStart=/bin/true apply" in text


def test_systemctl_missing_binary_returns_failed_result(monkeypatch) -> None:
    """On a host without systemd (container, other init), systemctl()
    returns a synthetic failure instead of raising FileNotFoundError, so
    `status`/`doctor` keep working."""
    monkeypatch.setattr(writer.shutil, "which", lambda _name: None)
    res = writer.systemctl("is-enabled", "trinity-pull.timer")
    assert res.returncode == 127
    assert "not found" in res.stderr


def test_render_service_includes_extended_hardening() -> None:
    text = systemd.render_service({"trinity_bin": "/bin/x", "home_dir": "/home/x"})
    for directive in (
        "SystemCallArchitectures=native",
        "RestrictRealtime=true",
        "LockPersonality=true",
        "ProtectKernelTunables=true",
        "UMask=0022",
    ):
        assert directive in text, directive


def test_render_service_omits_capability_dropping_directives() -> None:
    """ProtectClock=/ProtectKernelModules= are implemented by dropping
    capabilities, which a user manager cannot do — the unit then fails
    with 'Failed to drop capabilities' before ExecStart runs (seen on
    KDE Neon / Ubuntu 24.04). They must never reappear in the template.

    Line-anchored so the template comment explaining the omission
    doesn't count as a directive.
    """
    import re

    text = systemd.render_service({"trinity_bin": "/bin/x", "home_dir": "/home/x"})
    assert not re.search(r"^ProtectClock=", text, re.MULTILINE)
    assert not re.search(r"^ProtectKernelModules=", text, re.MULTILINE)


def test_pause_uses_runtime_mask() -> None:
    with patch.object(writer, "systemctl", return_value=_fake_process()) as mock:
        ok, msg = writer.pause()
    assert ok
    assert "runtime" in msg
    mock.assert_called_once_with("mask", "--runtime", "trinity-pull.timer")


def test_resume_uses_runtime_unmask() -> None:
    calls: list[tuple[str, ...]] = []

    def fake_systemctl(*args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _fake_process()

    with patch.object(writer, "systemctl", side_effect=fake_systemctl):
        ok, msg = writer.resume()
    assert ok
    assert "resumed" in msg
    assert any("--runtime" in c for c in calls)


def test_is_paused_detects_masked_state() -> None:
    with patch.object(writer, "systemctl", return_value=_fake_process("masked")):
        assert writer.is_paused() is True

    with patch.object(writer, "systemctl", return_value=_fake_process("enabled")):
        assert writer.is_paused() is False


def test_is_paused_detects_runtime_mask(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / "run" / "user" / "1000" / "systemd" / "user"
    runtime_dir.mkdir(parents=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir.parent.parent))
    link = runtime_dir / "trinity-pull.timer"
    link.symlink_to("/dev/null")
    with patch.object(writer, "systemctl", return_value=_fake_process("enabled")):
        assert writer.is_paused() is True
