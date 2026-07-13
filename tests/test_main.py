"""Tests for the ``python -m trinity`` entry point."""

from __future__ import annotations

import subprocess
import sys


def test_python_m_trinity_runs() -> None:
    """`python -m trinity --version` runs and prints the version string.

    Invoked via subprocess so the real `__main__.py` path is exercised
    (not just an import).
    """
    proc = subprocess.run(
        [sys.executable, "-m", "trinity", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "trinity " in proc.stdout


def test_python_m_trinity_help_lists_commands() -> None:
    """`python -m trinity --help` lists all subcommands."""
    proc = subprocess.run(
        [sys.executable, "-m", "trinity", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    for cmd in ("apply", "restore", "status", "config", "setup", "doctor"):
        assert cmd in proc.stdout, f"missing command in --help: {cmd}"
