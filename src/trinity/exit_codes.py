"""CLI exit-code convention for trinity.

Follows the spirit of BSD ``sysexits.h`` so users and shell scripts can
rely on stable, distinct exit codes per failure category. Numbers above
64 are reserved by C/POSIX for application-defined use; we follow the
most common Linux conventions:

- 0: success
- 1: generic runtime / backend / unexpected error
- 2: CLI usage error (missing args, conflicting flags)
- 65: data error (config invalid, manifest corrupted)
- 66: missing input (provider not found, file not found)
- 73: cannot create / refuse to overwrite existing file

``CLIError`` carries a ``status`` field that defaults to one of these
codes, and the CLI top-level handler converts the exception to the
right exit code.
"""

from __future__ import annotations

# Generic / runtime error. A surface backend failed, an HTTP request
# could not be completed, the manifest could not be written, etc.
EXIT_ERROR = 1

# CLI usage error: missing required argument, conflicting flags,
# invalid combination of options.
EXIT_USAGE = 2

# Data error: the TOML config is malformed or fails schema validation,
# the manifest is unparseable, the QML descriptor TOML is invalid.
EXIT_DATAERR = 65

# Missing input: a referenced provider, font, file, or unit is not
# found on the system.
EXIT_NOINPUT = 66

# Cannot create: refusing to overwrite an existing config or template
# file (caller must re-run with --force).
EXIT_CANTCREAT = 73

__all__ = [
    "EXIT_CANTCREAT",
    "EXIT_DATAERR",
    "EXIT_ERROR",
    "EXIT_NOINPUT",
    "EXIT_USAGE",
]
