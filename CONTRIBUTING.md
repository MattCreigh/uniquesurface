# Contributing to trinity

Thank you for your interest in contributing to trinity! This document
covers the development setup, code style, and quality gates.

## Development setup

trinity uses [uv](https://docs.astral.sh/uv/) for dependency management.

```sh
git clone https://github.com/MattCreigh/trinity.git
cd trinity
uv sync --extra test    # create venv + install dev + test deps
```

## Quality gates

All three must pass before a PR can be merged:

```sh
uv run ruff check src tests          # linting
uv run ruff format --check src tests # formatting (use --fix to auto-fix)
uv run mypy src                       # type checking (strict mode)
uv run pytest -q                      # tests (~1s, 114 tests)
```

## Code style

- **Python 3.12+** — use `from __future__ import annotations` for
  forward-reference type hints.
- **Type hints** on all public functions. mypy runs in strict mode
  (`disallow_untyped_defs`, `warn_return_any`, etc.).
- **Line length**: 88 characters (enforced by ruff).
- **Imports**: sorted by ruff (isort-compatible). Use `from x import y as y`
  for re-exports (mypy strict mode requires explicit re-exports).
- **Error handling**: use `CLIError` (with `hint=`) for user-facing errors,
  `BackendError` (with `hint=`) for backend failures. Broad `except Exception`
  is acceptable in plugin loading and CLI fallbacks but should be commented.
- **Subprocess calls**: always use explicit argv (no `shell=True`), always
  pass `timeout=`, use `check=True` for writes and `check=False` for reads.
- **File writes**: use `trinity.atomic.atomic_write_bytes` / `atomic_write_text`
  for all config/state writes (tmp + fsync + rename).

## Testing

- Tests run in isolation via `conftest.py` which redirects XDG dirs to a
  tmp path. Never write to real user config in tests.
- Use `respx` for HTTP mocking, `monkeypatch` for subprocess mocking.
- Integration tests (requiring a real Plasma session) should be marked
  `@pytest.mark.integration` and skipped when no display is available.
- Target: 80%+ coverage. Run `uv run pytest --cov=trinity --cov-report=term-missing`
  to check.

## Architecture overview

```
CLI (cli.py)
  → Config (config.py + schema.py)
  → Orchestrator (orchestrator.py)
      → Provider (providers/) fetches the image
      → verify_image (Pillow decode + re-encode)
      → Backends (backends/) write to each surface
      → QML Patcher (theme/qml_patch.py) patches vendor QML
      → Manifest (manifest.py) records every change for undo
```

Key design decisions are documented in `PLAN.md` (marked **DECIDED**).
Do not re-litigate decided items without flagging to the maintainer.

## Systemd unit hardening

The generated `trinity-pull.service` includes sandboxing directives
(`ProtectSystem`, `NoNewPrivileges`, `PrivateTmp`, `SystemCallFilter`,
etc.). When modifying the service template, ensure any new filesystem
paths trinity writes to are added to `ReadWritePaths`.

## License

By contributing, you agree that your contributions are licensed under the
PolyForm Noncommercial 1.0.0 license.