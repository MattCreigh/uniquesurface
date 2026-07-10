"""Structured logging setup.

JSON logs go to stdout and are captured by the systemd journal
(``journalctl --user -u trinity-pull.service``).

This module is named ``logging_setup`` rather than ``logging`` to avoid
shadowing the stdlib ``logging`` module.  The previous name worked (absolute
imports win) but confused tooling and readers.  No compatibility shim is
provided — this is a 0.1.0 application, not a library API.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog to emit JSON to stdout.

    ``cache_logger_on_first_use=False`` is critical: with it set to
    ``True`` (structlog's default), a module-level ``_log =
    get_logger(__name__)`` *caches* the unconfigured stdlib-fallback
    state the first time it is used (which happens before the CLI
    entry point calls :func:`configure_logging`), and every later
    call on that same proxy then re-uses the cached stdlib logger —
    which rejects arbitrary keyword arguments like ``hint=`` with a
    ``TypeError: Logger._log() got an unexpected keyword argument
    'hint'``.  This bites every pre-existing ``_log.warning(...,
    hint=...)`` call.  We resolve the proxy on every call instead.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    """Return a bound structlog logger.

    Typed as ``FilteringBoundLogger`` to match the ``wrapper_class``
    installed by :func:`configure_logging`.
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]
