"""Post-patch QML validation.

After a QML file is patched, run ``qmllint`` on it if available.
This is a *fail-closed* gate: if the linter reports any error, the
patch is rolled back via the manifest machinery and the failure is
surfaced as a structured plan line, so other surfaces (e.g. the SDDM
theme config) still apply.

Why this is a fail-closed gate
==============================

A QML syntax error introduced by a trinity patch would cause
``kscreenlocker_greet`` (the SDDM greeter, and the lock screen) to
fall back to the built-in blue locker.  That fallback is silent and
*exactly the failure mode that bit us when ``pragma Singleton`` was
appended to a non-singleton file* (see the qml_patch module
docstring).  The right response to a QML syntax error is not "log a
warning and proceed" but "refuse the patch, restore the previous
bytes, and tell the user".

Availability
============

``qmllint`` is shipped by:

* **Debian / Neon / Kubuntu** — ``qml6-qttools`` (or
  ``qml6-qtdeclarative-tools`` on older releases).
* **Fedora / RHEL** — ``qt6-qtdeclarative-devel``.
* **Arch Linux** — ``qt6-declarative`` (in the official repos).

The helper does not auto-install — it just records availability
once, so the user can install the package if they want stricter
validation.

Timeout
=======

Linting is bounded at 5 s per file.  A vendor file rarely takes
more than a second on a real machine; the cap is a safety net for
hung subprocesses.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from trinity.logging_setup import get_logger

_log = get_logger(__name__)

_TIMEOUT_SECONDS = 5.0

# Cached path to the ``qmllint`` binary.  ``None`` means "we have
# looked and did not find it"; a string means "we found it here";
# the literal ``False`` is the "not yet searched" sentinel.
_qmllint_path: str | None | bool = False  # sentinel: not yet searched


def qmllint_available() -> str | None:
    """Return the absolute path to ``qmllint`` if available, else ``None``.

    Cached per process; the first lookup shells out to ``which`` and
    records the result.  A missing binary is a normal state (a fresh
    container, a CI runner) — the helper logs once and returns
    ``None`` on subsequent calls.
    """
    global _qmllint_path
    if _qmllint_path is False:
        path = shutil.which("qmllint")
        if path is None:
            _log.info(
                "qmllint_not_found",
                hint=(
                    "Install qml6-qttools (Debian/Neon) or "
                    "qt6-qtdeclarative-devel (Fedora) or "
                    "qt6-declarative (Arch) to enable "
                    "post-patch validation."
                ),
            )
            _qmllint_path = None
        else:
            _qmllint_path = path
    # After the first lookup, _qmllint_path is str | None; the False
    # sentinel only survives if which() somehow returns it (it
    # doesn't), but mypy doesn't know that, so narrow explicitly.
    return _qmllint_path if isinstance(_qmllint_path, str) else None


@dataclass(frozen=True)
class QmlLintResult:
    """Outcome of a single ``qmllint`` invocation.

    ``ok`` is True iff the linter exited 0 (no errors, no warnings).
    ``stdout`` / ``stderr`` are the captured streams, useful in
    error messages.
    """

    ok: bool
    stdout: str
    stderr: str
    timed_out: bool


def lint_file(path: Path) -> QmlLintResult:
    """Run ``qmllint`` on ``path`` and return the result.

    Returns a "not available" result (with ``ok=True``) when the
    linter is missing — we never block a patch on a missing
    optional tool, but the apply path logs the absence.
    """
    binary = qmllint_available()
    if binary is None:
        return QmlLintResult(ok=True, stdout="", stderr="", timed_out=False)
    try:
        proc = subprocess.run(
            [binary, str(path)],
            timeout=_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return QmlLintResult(
            ok=False,
            stdout="",
            stderr=f"qmllint timed out after {_TIMEOUT_SECONDS}s",
            timed_out=True,
        )
    except OSError as exc:
        return QmlLintResult(
            ok=False,
            stdout="",
            stderr=f"qmllint failed to start: {exc}",
            timed_out=False,
        )
    ok = proc.returncode == 0
    return QmlLintResult(ok=ok, stdout=proc.stdout, stderr=proc.stderr, timed_out=False)


__all__ = [
    "QmlLintResult",
    "lint_file",
    "qmllint_available",
]
