"""Tests for the CLI exit-code convention."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from trinity.cli import main
from trinity.exit_codes import (
    EXIT_CANTCREAT,
    EXIT_DATAERR,
    EXIT_ERROR,
    EXIT_NOINPUT,
    EXIT_USAGE,
)


def test_exit_code_constants_match_sysexits_convention() -> None:
    """The named codes are stable and follow sysexits.h spirit."""
    # 0 = success (omitted: implicit, not a constant).
    assert EXIT_ERROR == 1
    assert EXIT_USAGE == 2
    # 64+ are application-defined per BSD sysexits.h.
    assert EXIT_DATAERR == 65
    assert EXIT_NOINPUT == 66
    assert EXIT_CANTCREAT == 73


def test_install_without_config_exits_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`trinity install` with no config and no --yes signals EXIT_USAGE (2)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # No config in tmp_path → install should refuse.
    result = CliRunner().invoke(main, ["install"])
    assert result.exit_code == EXIT_USAGE
    assert "config" in result.output.lower() or "setup" in result.output.lower()


def test_install_with_yes_skips_usage_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`trinity install --yes` proceeds past the usage gate.

    It may still fail later (no systemd, no fontconfig, etc.) but it
    must not exit with EXIT_USAGE because the user explicitly opted
    into the non-interactive flow.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = CliRunner().invoke(main, ["install", "--yes"])
    assert result.exit_code != EXIT_USAGE
