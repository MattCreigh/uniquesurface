# Contributing to trinity

Thank you for your interest in contributing to trinity! This document
covers the development setup, code style, and quality gates.

## Development setup

trinity uses [uv](https://docs.astral.sh/uv/) for dependency management.

```sh
git clone https://github.com/MattCreigh/uniquesurface.git
cd trinity
uv sync --group test    # create venv + install dev + test deps
```

## Quality gates

All four must pass before a PR can be merged (CI enforces them on every
push and pull request):

```sh
uv run ruff check src tests          # linting
uv run ruff format --check src tests # formatting
uv run mypy src                      # type checking (strict mode)
uv run pytest -q                     # tests
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
- Coverage floor: 75% (enforced via `[tool.coverage.report] fail_under`
  when running with `--cov`); target 80%+. Run
  `uv run pytest --cov --cov-report=term-missing` to check. New code
  should cover its error paths, not just the success path.

## Pull requests

- Branch from `main`; keep PRs focused on one change.
- All CI checks (lint, format, types, tests on every supported Python)
  must be green before merge — `main` is a protected branch.
- Update `CHANGELOG.md` under `[Unreleased]` for user-visible changes.

## Releases

1. Update the version in `src/trinity/__init__.py` (the single source of
   truth — `pyproject.toml` reads it via `[tool.hatch.version]`).
2. Move the `[Unreleased]` CHANGELOG section under the new version.
3. Tag `vX.Y.Z` and push the tag; build artefacts with `uv build`.
   (The project is not published to PyPI; install from the repository.)

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
GPL-3.0-or-later license.