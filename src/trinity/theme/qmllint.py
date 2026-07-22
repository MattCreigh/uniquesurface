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

PATH presence is not enough: on Debian-family systems the bare
``qmllint`` name can be a qtchooser shim that fails on *every*
invocation when only Qt 6 is installed (``could not exec
'/usr/lib/qt5/bin/qmllint'``).  A fail-closed gate fed by such a
shim would read "every patch is broken" and revert all patches, so
each candidate is probed with ``--version`` and only a binary that
actually runs is used.  The unambiguous Qt 6 name ``qmllint6`` is
preferred — the patched QML is Plasma 6 / Qt 6.

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

# Most specific first: ``qmllint6`` is the unambiguous Qt 6 name on
# Debian-family systems, where bare ``qmllint`` may be a Qt 5
# qtchooser shim (see module docstring).
_QMLLINT_CANDIDATES = ("qmllint6", "qmllint")


def _locate_working_qmllint() -> str | None:
    """Find the first candidate binary that both exists and runs.

    Probes with ``--version``: a qtchooser shim whose Qt 5 target is
    not installed exits non-zero on any invocation, and trusting it
    would make the fail-closed lint gate revert every patch.
    """
    for name in _QMLLINT_CANDIDATES:
        path = shutil.which(name)
        if path is None:
            continue
        try:
            proc = subprocess.run(
                [path, "--version"],
                timeout=_TIMEOUT_SECONDS,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return path
        _log.info(
            "qmllint_probe_failed",
            path=path,
            stderr=proc.stderr.strip(),
        )
    _log.info(
        "qmllint_not_found",
        hint=(
            "Install qml6-qttools (Debian/Neon) or "
            "qt6-qtdeclarative-devel (Fedora) or "
            "qt6-declarative (Arch) to enable "
            "post-patch validation."
        ),
    )
    return None


def qmllint_available() -> str | None:
    """Return the absolute path to a *working* ``qmllint``, else ``None``.

    Cached per process; the first lookup searches PATH and probes the
    result (see :func:`_locate_working_qmllint`).  A missing binary is
    a normal state (a fresh container, a CI runner) — the helper logs
    once and returns ``None`` on subsequent calls.
    """
    global _qmllint_path
    if _qmllint_path is False:
        _qmllint_path = _locate_working_qmllint()
    # After the first lookup, _qmllint_path is str | None; the False
    # sentinel never survives _locate_working_qmllint, but mypy doesn't
    # know that, so narrow explicitly.
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

    Returns a "not available" result (with ``ok=False``) when the
    linter is missing.  The fail-closed gate in the orchestrator
    handles this by reverting the patch unless ``skip_qmllint`` is set
    in the config.
    """
    binary = qmllint_available()
    if binary is None:
        return QmlLintResult(
            ok=False,
            stdout="",
            stderr=(
                "qmllint not found; install qml6-qttools (Debian/Neon), "
                "qt6-qtdeclarative-devel (Fedora), or qt6-declarative (Arch) "
                "to validate patches, or set surface.theme_tokens.skip_qmllint "
                "= true to bypass at your own risk"
            ),
            timed_out=False,
        )
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
