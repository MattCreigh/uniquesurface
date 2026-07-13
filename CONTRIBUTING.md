# Contributing to trinity

Thank you for your interest in trinity — a unified Plasma 6 surface
manager. This guide covers the local dev setup, the quality gates, and
the conventions new contributors need to know.

## Quick start

```bash
git clone <repo>
cd background_manager
uv sync --group dev --group test
uv run pytest -q
```

`uv` handles the Python dependency graph (Python 3.12+); the lockfile is
the source of truth, so `--frozen` is implied for CI runs.

## Quality gates

All four must pass locally before opening a PR. CI runs the same
sequence on push/PR against Python 3.12 and 3.13.

```bash
# Lint
uv run ruff check src tests
uv run ruff format --check src tests

# Type-check
uv run mypy src

# Tests
uv run pytest -q
```

The 75% coverage floor (configured in `pyproject.toml` under
`[tool.coverage.report]`) must remain met. New code should ship with
its own tests; the coverage gate will fail otherwise.

## Exit-code convention

The CLI uses BSD `sysexits.h`-style codes. The named constants live in
`trinity.exit_codes`; do not introduce new `sys.exit(N)` literals.

| Constant | Code | Meaning |
|---|---|---|
| `EXIT_ERROR` | 1 | Generic runtime / backend / unexpected error |
| `EXIT_USAGE` | 2 | CLI usage error (missing argument, conflicting flags) |
| `EXIT_DATAERR` | 65 | Bad config (TOML parse, schema validation) |
| `EXIT_NOINPUT` | 66 | Missing provider, font, file, unit |
| `EXIT_CANTCREAT` | 73 | Refusing to overwrite existing file |

When you need a new category, add it to `exit_codes.py` with a
matching test in `tests/test_exit_codes.py`.

## Adding a new provider

Providers are pluggy plugins. The minimum surface area is three
hooks: `trinity_provider_name`, `trinity_provider_info`,
`trinity_provider_fetch`. For a full integration the provider should
also expose `trinity_provider_options_schema` (a pydantic `BaseModel`
with `model_config = ConfigDict(extra="forbid")`) and
`trinity_provider_probe` (return a cheap change-token or `None`).

Use `trinity.providers.builtin._http` for HTTP — it already does HTTPS
enforcement, SSRF defense, size caps, and redirect handling.

## Adding a new surface backend

Subclass `trinity.backends.base.Backend` and implement `apply()` and
`dry_run_plan()`. Register the new backend in
`trinity.orchestrator.default_backends()` if it should run by default.

The orchestrator catches `BackendError` per backend so a failure in
one surface never blocks the others. Use `BackendError(hint=...)` to
surface a remediation hint to the user.

## QML descriptors

Theme-token patches are data-driven via TOML descriptors in
`src/trinity/theme/descriptors/`. Each descriptor declares:

- A Plasma version range (`plasma = ">=6.0,<6.8"`)
- One or more patches (`kind = "font_property" | "fadeout_timer" | "wake_guard"`)
- A compiled regex `anchor.pattern` for the rewrite location
- A literal `insert_block` (with `{indent}` placeholder) for inserts

The upstream-canary workflow (`.github/workflows/upstream-canary.yml`)
runs weekly against real KDE source to detect when a Plasma update
moves a managed property. A new descriptor is the fix for any
upstream rename.

## Security model

trinity runs as the invoking user, optionally with `sudo` for system
paths. Treat third-party provider plugins as a supply-chain surface
(loaded via `importlib.metadata.entry_points()`). The HTTP layer
already:

- Rejects non-HTTPS URLs
- Resolves DNS pre-flight and blocks private/loopback/link-local
- Caps redirects at 5
- Caps metadata at 5 MiB, images at 50–100 MiB
- Parses XML with `defusedxml` (no XXE / billion-laughs)

New code that touches the network or the filesystem should follow
the same posture: validate at the boundary, fail closed, and log
with the exception class name (not the message alone) so the
operator can identify the offender.

## Commit messages

Use the imperative mood in the subject line:

```
Fix adopt_drift: re-apply lock tokens after MainBlock.qml drift
```

A scope prefix is encouraged when the change is local to one module:

```
cli: unify exit codes against sysexits.h
orchestrator: brace-balanced wake-guard removal
```

## Pull requests

- One logical change per PR.
- The PR description should link any related issue.
- New user-facing behavior needs a `CHANGELOG.md` entry under
  `[Unreleased]`.
- A reviewer's time is precious — local CI green before requesting
  review.
