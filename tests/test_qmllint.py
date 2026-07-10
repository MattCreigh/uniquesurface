"""Tests for the post-patch qmllint validation gate.

Phase 4 added a fail-closed gate: after a trinity patch writes a
QML file, ``qmllint`` is run on it (if available) and any lint
failure rolls the file back to its pristine state via the manifest.
These tests exercise the helper in isolation.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from trinity.theme import qmllint
from trinity.theme.qmllint import QmlLintResult, lint_file, qmllint_available


@pytest.fixture(autouse=True)
def _reset_qmllint_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the ``shutil.which`` cache so each test sees a fresh PATH."""
    monkeypatch.setattr(qmllint, "_qmllint_path", False)


def test_qmllint_available_returns_path_when_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``shutil.which("qmllint")`` returns a path, we surface it."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/qmllint")
    assert qmllint_available() == "/usr/bin/qmllint"


def test_qmllint_available_returns_none_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``shutil.which("qmllint")`` returns None, we surface None."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert qmllint_available() is None


def test_qmllint_available_caches_its_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PATH lookup runs once per process; subsequent calls are cached."""
    calls: list[str] = []

    def fake_which(name: str) -> str | None:
        calls.append(name)
        return "/usr/bin/qmllint"

    monkeypatch.setattr(shutil, "which", fake_which)
    qmllint_available()
    qmllint_available()
    qmllint_available()
    assert calls == ["qmllint"]


def test_lint_file_returns_ok_when_linter_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If qmllint is absent, ``lint_file`` returns ok=True (no-op)."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = lint_file(tmp_path / "Login.qml")
    assert result.ok is True
    assert result.timed_out is False


def test_lint_file_passes_clean_qml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A valid QML file (per a fake linter) returns ok=True."""

    def fake_which(name: str) -> str:
        return "/usr/bin/qmllint"

    monkeypatch.setattr(shutil, "which", fake_which)

    real_run = subprocess.run

    def fake_run(argv, **kw):  # type: ignore[no-untyped-def]
        # Only intercept the qmllint call; let everything else through.
        if argv and isinstance(argv, list) and argv[0] == "/usr/bin/qmllint":
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout="", stderr=""
            )
        return real_run(argv, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    target = tmp_path / "Login.qml"
    target.write_text("import QtQuick\nItem {}\n")
    assert lint_file(target).ok is True


def test_lint_file_fails_on_lint_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A qmllint non-zero exit surfaces ok=False with the stderr text."""

    def fake_which(name: str) -> str:
        return "/usr/bin/qmllint"

    monkeypatch.setattr(shutil, "which", fake_which)
    real_run = subprocess.run

    def fake_run(argv, **kw):  # type: ignore[no-untyped-def]
        if argv and isinstance(argv, list) and argv[0] == "/usr/bin/qmllint":
            return subprocess.CompletedProcess(
                args=argv,
                returncode=1,
                stdout="",
                stderr="Login.qml:5: syntax error",
            )
        return real_run(argv, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    target = tmp_path / "Login.qml"
    target.write_text("broken qml")
    result = lint_file(target)
    assert result.ok is False
    assert "syntax error" in result.stderr
    assert result.timed_out is False


def test_lint_file_marks_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A qmllint that exceeds the timeout is reported as ok=False, timed_out=True."""

    def fake_which(name: str) -> str:
        return "/usr/bin/qmllint"

    monkeypatch.setattr(shutil, "which", fake_which)

    def fake_run(argv, **kw):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout", 5.0))

    monkeypatch.setattr(subprocess, "run", fake_run)
    target = tmp_path / "Login.qml"
    target.write_text("import QtQuick\nItem {}\n")
    result = lint_file(target)
    assert result.ok is False
    assert result.timed_out is True


def test_lint_file_marks_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A qmllint that fails to start (OSError) is ok=False, timed_out=False."""

    def fake_which(name: str) -> str:
        return "/usr/bin/qmllint"

    monkeypatch.setattr(shutil, "which", fake_which)

    def fake_run(argv, **kw):  # type: ignore[no-untyped-def]
        raise OSError("permission denied")

    monkeypatch.setattr(subprocess, "run", fake_run)
    target = tmp_path / "Login.qml"
    target.write_text("import QtQuick\nItem {}\n")
    result = lint_file(target)
    assert result.ok is False
    assert result.timed_out is False
    assert "permission denied" in result.stderr


def test_qml_lint_result_is_frozen() -> None:
    """QmlLintResult is a frozen dataclass — instances are immutable."""
    r = QmlLintResult(ok=True, stdout="", stderr="", timed_out=False)
    with pytest.raises((AttributeError, Exception)):
        r.ok = False  # type: ignore[misc]
