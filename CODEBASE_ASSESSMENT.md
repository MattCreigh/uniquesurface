# usurface (background_manager) Codebase Assessment Report

**Prepared:** 2026-07-01
**Repository:** /home/matt/Projects/background_manager
**Package:** uniquesurface (src/usurface)
**Version:** 0.0.0 (pre-release)
**License:** PolyForm Noncommercial 1.0.0
**Python:** 3.12+
**Lines of Code:** ~3,934 (src + tests, excluding __pycache__)

---

## 1. Executive Summary

## Implementation Status

As of 2026-07-01, the report recommendations have been implemented and the package has been built, installed, and tested:

- `pyproject.toml` now declares a `[dependency-groups] dev` group with `ruff`, `mypy`, and `fonttools`.
- Code quality issues resolved:
  - Fixed missing `Path` import in `tests/test_paths.py`.
  - Fixed `Source(options=...)` type mismatch in `src/usurface/cli.py`.
  - Fixed `ProviderPlugin` Protocol empty-body errors in `src/usurface/providers/__init__.py`.
  - Removed unused imports and reformatted all 45 Python files.
- `src/usurface/theme/drift.py` updated so `handle_drift` re-extracts the pristine template from the actual vendor file, rather than accepting the drifted on-disk file as the new baseline.
- A bundled `Inter-Regular.ttf` placeholder font is now included at `src/usurface/theme/fonts/Inter-Regular.ttf` (copied from the system Lato font for packaging purposes; replace with the real Inter font before production use).
- Version bumped to `0.1.0` in both `pyproject.toml` and `src/usurface/__init__.py`.
- Wheel and sdist built successfully:
  - `dist/uniquesurface-0.1.0-py3-none-any.whl`
  - `dist/uniquesurface-0.1.0.tar.gz`
- Installed on the PC with `uv tool install --force dist/uniquesurface-0.1.0-py3-none-any.whl`; executable at `/home/matt/.local/bin/usurface`.
- End-to-end tests:
  - `usurface --version` returns `uniquesurface 0.1.0`.
  - `usurface provider list` shows bing/file/solid.
  - `usurface config init` + `config validate` succeed.
  - `usurface apply --dry-run` with the `solid` provider produces a correct plan.
  - A real `usurface apply` correctly creates `last_wallpaper.jpg`, writes the manifest, updates `kscreenlockerrc` and desktop appletsrc, and stops with a clear `PermissionError` when it reaches the root-owned SDDM `theme.conf` (expected without `sudo`).
- Quality gates are now green:
  - `uv run ruff check src tests` → All checks passed!
  - `uv run ruff format --check src tests` → 45 files already formatted
  - `uv run mypy src` → Success: no issues found in 31 source files
  - `uv run --extra test pytest -q` → 78 passed

Remaining optional items not yet implemented:
- `usurface pause` command or explicit timer-disable documentation.
- Configurable SDDM/QML paths (currently hard-coded to Breeze defaults).
- An `@pytest.mark.integration` test skeleton.



`uniquesurface` is a Python CLI application that manages a cohesive set of wallpapers across three KDE Plasma 6 surfaces: the desktop wallpaper, the lock screen wallpaper, and the SDDM/plasmalogin login screen wallpaper. It is designed as a reversible, atomic, idempotent, and systemd-automated replacement for ad-hoc shell scripts.

The codebase is functionally complete for a v0.1 release: all major subsystems are implemented, the test suite passes, and the architecture is clean and modular. However, it exhibits several code-quality issues typical of a pre-release AI-assisted project: missing development tooling in the lockfile (ruff, mypy), numerous unused imports, formatting drift, and a small number of type-check errors.

---

## 2. Purpose and Functionality

### 2.1 Problem Domain

KDE Plasma 6 stores wallpaper and visual styling configuration in multiple, heterogeneous locations:
- Desktop wallpaper: `~/.config/plasma-org.kde.plasma.desktop-appletsrc`
- Lock screen wallpaper: `~/.config/kscreenlockerrc`
- Login screen wallpaper: `/usr/share/sddm/themes/breeze/theme.conf` and patched QML files

Existing tools typically handle only the desktop. `uniquesurface` unifies these into a single configuration-driven workflow.

### 2.2 Core Capability

1. **Unified apply:** Fetch or generate a wallpaper, verify it, then write it to all three surface configurations in one command.
2. **Provider system:** Pluggable image sources (Bing POTD, local file, solid colour/gradient).
3. **Atomic operations:** Every file change is written through a temporary file + fsync + rename pattern.
4. **Reversibility:** Every change is recorded in an append-only JSONL manifest; `restore` reverts newest-first.
5. **QML patching:** Patches SDDM and Plasma lock-screen QML with font/theme tokens using sentinel markers and drift detection.
6. **Automation:** Daily systemd user timer for refreshing picture-of-the-day sources.
7. **Migration:** Detects legacy shell-based setups and generates starter configuration.

---

## 3. Architecture

### 3.1 High-level Structure

```
usurface (CLI through click)
├── config (TOML loader, Pydantic schema)
├── providers (pluggy-based plugin registry + built-ins)
├── orchestrator (fetch + verify + dispatch to backends)
├── backends (desktop, lock, login writers)
├── theme (QML template extraction, drift detection, font install, QML patching)
├── systemd (user unit rendering and lifecycle)
├── manifest (append-only undo log)
├── atomic (atomic file I/O)
├── paths (XDG-aware path resolution)
└── logging (structlog JSON logging)
```

### 3.2 Data Flow on `usurface apply`

1. Load and validate `~/.config/usurface/config.toml`.
2. Resolve source through provider plugin → fetch image bytes.
3. Verify image with Pillow (decode + re-encode, stripping metadata).
4. Write canonical copy to `~/.local/state/usurface/last_wallpaper.jpg`.
5. Mirror to `shared_dir/last_wallpaper.jpg` (default `/usr/local/share/wallpapers/`) with mode 0644.
6. Backend writers update desktop (`appletsrc` through `kwriteconfig6` + `qdbus6`), lock (`kscreenlockerrc` through `kwriteconfig6`), and login (`theme.conf` through regex edit).
7. QML patcher applies font/theme tokens to SDDM `Login.qml` and Plasma lock-screen QML using sentinel markers, after drift detection.
8. Each operation is recorded in `~/.local/state/usurface/manifest.jsonl`.

---

## 4. Module-by-Module Breakdown

### 4.1 CLI (`src/usurface/cli.py`, 468 lines)

Entry point: `usurface`.
Commands/groups:
- `apply [--dry-run]` — main workflow.
- `restore [--to] [--yes]` — undo manifest entries.
- `status` — show config/manifest state.
- `install [--yes]` — font install, shared dir, systemd timer.
- `uninstall [--yes]` — remove systemd units.
- `config {show,validate,init}` — config inspection/creation.
- `provider {list,info}` — list available providers.
- `qml-update-templates --yes` — re-extract pristine QML.
- `doctor` — health checks.
- `migrate-from-shell --dry-run` — detect legacy setup and write starter config.

### 4.2 Configuration (`src/usurface/schema.py`, `src/usurface/config.py`)

Pydantic v2 models enforce a strict TOML schema. Sections:
- `[surface]` with `schema_version`
- `[surface.source]` — provider + options
- `[surface.fonts]` — family, weight, password_character
- `[surface.login]` — clock_format, accent_color, show_user_list
- `[surface.lock]` — on_idle_dim_seconds, suppress_wake_keypress
- `[surface.behaviour]` — shared_dir, user_dir

Validation is strict (`extra="forbid"`) to catch typos.

### 4.3 Provider Registry (`src/usurface/providers/`)

Uses `pluggy` for plugin loading. Built-ins:
- `bing.py` — Bing Picture of the Day through `httpx` and `www.bing.com/HPImageArchive.aspx`.
- `file.py` — read local image file, infer content type from extension.
- `solid.py` — generate solid colour or vertical gradient JPEG through Pillow.

Plugin interface: `usurface_provider_name`, `usurface_provider_info`, `usurface_provider_fetch`.

### 4.4 Orchestrator (`src/usurface/orchestrator.py`)

Coordinates the apply pipeline:
- `fetch_wallpaper(config)` → dispatch provider.
- `verify_image(data)` → Pillow decode/re-encode.
- `apply_to_surfaces(config, manifest, backends, dry_run)` → run backends + QML patcher.

### 4.5 Backends (`src/usurface/backends/`)

- `base.py` — `Backend` protocol.
- `desktop.py` — writes `plasma-org.kde.plasma.desktop-appletsrc` through `kwriteconfig6`, refreshes through `qdbus6`.
- `lock.py` — writes `kscreenlockerrc` through `kwriteconfig6`.
- `login.py` — edits `/usr/share/sddm/themes/breeze/theme.conf` `background=` line; checks root permissions.
- `_kconfig.py` — helpers for `kwriteconfig6` and `qdbus6` shell-outs.

### 4.6 Theme/QML (`src/usurface/theme/`)

- `extract.py` — copy pristine vendor QML into `~/.local/state/usurface/templates/`.
- `drift.py` — detect upstream changes by comparing SHA-256 of stripped on-disk QML vs. stored pristine; creates drift backups and can re-extract.
- `qml_patch.py` — append sentinel markers (`/* @usurface:start */` … `/* @usurface:end */`) and replace the region with rendered font/theme tokens.
- `font_install.py` — install Inter-Regular.ttf to `/usr/local/share/fonts/usurface/` (or `~/.local/share/fonts/` fallback), run `fc-cache`.
- `tokens.py` — default token factories.

### 4.7 Systemd (`src/usurface/systemd/`)

- Renders `usurface-pull.service` and `usurface-pull.timer` from Jinja2 templates.
- `install()` writes units to `~/.config/systemd/user/`.
- `enable_and_start()` / `disable_and_stop()` run `systemctl --user`.
- Handles `sudo` context by running `systemctl` as `SUDO_USER`.

### 4.8 Manifest (`src/usurface/manifest.py`)

Append-only JSONL undo log. Each entry records:
- timestamp, operation (`write`/`delete`), path, previous/new SHA-256, snapshot path.

`write_tracked()` performs atomic writes while recording snapshots.
`restore()` replays inverse operations newest-first.
`truncate()` clears the log after a verified restore.

### 4.9 Atomic I/O (`src/usurface/atomic.py`)

Three helpers:
- `atomic_write_bytes`
- `atomic_write_text`
- `atomic_replace_with`

Pattern: sibling temp file → write → fsync → chmod → `os.replace` → cleanup temp on failure.

### 4.10 Paths (`src/usurface/paths.py`)

XDG-aware path resolution using `platformdirs`. Handles `SUDO_USER` so operations under `sudo` write to the original user's directories. Shared wallpaper directory is overridable through `USURFACE_SHARED_DIR`.

### 4.11 Logging (`src/usurface/logging.py`)

`structlog` configured for JSON output to stdout, suitable for systemd journal capture.

---

## 5. Quality Assessment

### 5.1 Test Suite

**Result:** 78 passed in ~0.63s.

Coverage areas:
- Atomic writes (success, overwrite, failure cleanup, mode, writer callback).
- Config parsing and validation.
- Provider registry and all built-ins.
- Backend writers (desktop, lock, login) with mocked `kwriteconfig6`/`qdbus6`.
- Manifest append/restore/snapshot behaviour.
- QML patching and sentinel handling.
- Drift detection and re-extraction.
- Systemd unit rendering.
- CLI end-to-end with `click.testing.CliRunner` and `respx` HTTP mocks.

### 5.2 Static Analysis

Ruff, mypy, and ruff-format are **not declared as project dependencies**, so `uv run ruff`/`uv run mypy` fail with “No such file or directory.” When installed ad-hoc with `uv run --with ruff` / `--with mypy`:

- **Ruff check:** 24 errors, mostly unused imports in tests; one redefinition in `test_cli.py` and `test_drift.py`; `test_paths.py` missing `from pathlib import Path`.
- **Ruff format:** 24 of 45 Python files would be reformatted.
- **mypy:** 4 errors:
  - `src/usurface/providers/__init__.py:62,67,72` — empty-body Protocol methods flagged as missing return statements.
  - `src/usurface/cli.py:367` — `Source(options={...})` receives `dict[str, str]` instead of `SourceOptions`.

### 5.3 Code Issues and Inconsistencies

1. **Missing dev dependencies.** `pyproject.toml` does not include `ruff`, `mypy`, or `pytest` in `[project.optional-dependencies]` / `[dependency-groups]` dev. This breaks the documented `uv`-first workflow.
2. **Test module `test_paths.py` is broken.** It uses `tmp_path: Path` but never imports `Path`.
3. **Unused imports across tests.** High noise; indicates tests were not linted.
4. **Formatting not enforced.** ~53% of Python files need reformatting.
5. **CLI `Source(options=...)` type mismatch.** `migrate-from-shell` builds a `Source` with a plain dict; pydantic may coerce it but mypy flags it.
6. **Protocol empty-body errors.** The `ProviderPlugin` Protocol uses `...` stubs; mypy strict flags them. Adding `@abstractmethod` or `# type: ignore` is needed.
7. **Hard-coded paths and assumptions.**
   - SDDM theme path is hard-coded to `/usr/share/sddm/themes/breeze/theme.conf`.
   - QML targets are hard-coded to specific vendor paths.
   - These are acceptable per PLAN scope (SDDM Breeze only) but limit portability.
8. **Font vendoring incomplete.** `theme/font_install.py` looks for `Inter-Regular.ttf` but no such file is present in the repo. The `CHANGELOG.md` explicitly notes “No vendored Inter font bundled.”
9. **`usurface install` does not create shared dir as root correctly.** The CLI calls `sw.mkdir(...)` without sudo elevation; if the parent `/usr/local/share` is not writable by the user, it will fail with a permission error rather than elevating.
10. **`_to_toml` serializer is naive.** It cannot round-trip all TOML structures perfectly (e.g., nested inline tables, arrays of tables, dates). For the current flat schema it works, but it is a maintenance risk if the schema grows.
11. **Drift handling writes a backup but the `handle_drift` docstring claims to re-extract and update pristine; implementation updates pristine from the already-drifted on-disk file, which effectively accepts the drift as the new baseline rather than re-extracting the actual vendor file.**
12. **QML patch `FontPatch.render_block()` emits a singleton QtObject pragma that may not be valid QML in all target files; it is appended as a comment block, not semantically integrated.**
13. **Desktop backend manifest snapshot race.** It snapshots the config file before `kwriteconfig6` runs, then records the new SHA after. If the file is modified concurrently, the recorded snapshot may not match what was restored.
14. **No integration tests marked.** `pyproject.toml` defines `@pytest.mark.integration`, but no tests use it.
15. **Version is 0.0.0.** `__init__.py` and `pyproject.toml` disagree with README badge and `PLAN.md` v0.1.0 target.

---

## 6. Design Patterns and Conventions

- **Plugin architecture** through `pluggy` hookspecs/hookimpls.
- **Protocol/duck typing** for backends and provider plugins.
- **Command pattern** for CLI commands with `click` groups.
- **Repository/state pattern** for manifest and config stores.
- **Template method** in orchestrator: fetch → verify → write → backends → QML.
- **Atomic writes** with temp-then-rename and fsync.
- **Snapshot/undo log** for reversibility.
- **Dependency injection** in tests through monkeypatch and CliRunner.

---

## 7. Dependencies

Runtime (from `pyproject.toml`):
- pydantic >=2.6,<3
- httpx >=0.27,<1
- Pillow >=10.3,<12
- platformdirs >=4.2,<5
- structlog >=24.1,<25
- pluggy >=1.5,<2
- click >=8.1,<9
- Jinja2 >=3.1,<4

Test/optional (from `pyproject.toml`):
- pytest, pytest-cov, respx, syrupy

Missing from project metadata but used in workflow:
- ruff, mypy

---

## 8. Entry Points

- Console script: `usurface = usurface.cli:main`
- Module invocation: `python -m usurface`
- Direct import: `from usurface.cli import main; main()`

---

## 9. Documentation

- `README.md` — quickstart and feature overview.
- `PLAN.md` — comprehensive design spec, architecture, phases, decisions.
- `docs/config-reference.md` — every config key documented.
- `docs/migration-from-shell.md` — migration from legacy bash setup.
- `CHANGELOG.md` — Keep-a-Changelog format, currently unreleased.
- `src/usurface/providers/README.md` — third-party provider authoring guide.

Documentation is thorough and professional, with clear warnings about root requirements and migration order.

---

## 10. Security and Operational Considerations

- **Privilege boundaries:** Normal `apply` is user-level; `install` requires root for font/shared dir/QML patching. The code correctly tries to avoid sudo for daily use.
- **Atomicity:** File writes are atomic, reducing the risk of corrupted Plasma config on crash.
- **Reversibility:** Manifest + snapshots allow rollback, but snapshots live in `~/.local/state/usurface/manifest_snapshots/`; if deleted, restore becomes impossible for existing entries.
- **Network:** Bing provider uses `httpx` with a browser-like user-agent. No certificate pinning or proxy handling is explicit.
- **Third-party plugins:** Only built-ins are loaded by default; third-party providers through entry points are trusted once registered.
- **QML safety:** Sentinel markers and drift detection reduce the chance of bricking the login screen after a Plasma update, but the implementation accepts drift as the new baseline rather than forcing manual reconciliation.

---

## 11. Recommendations

1. Add `ruff`, `mypy`, and `pytest` to a `[dependency-groups]` `dev` group or `[project.optional-dependencies]` `dev`.
2. Fix `test_paths.py` by adding `from pathlib import Path`.
3. Run `ruff check --fix` and `ruff format` across the codebase; add a CI/pre-commit check.
4. Resolve the `Source(options=...)` type issue and the Protocol empty-body mypy errors.
5. Bundle `Inter-Regular.ttf` or remove the hard-coded Inter expectation and improve fallback messaging. (Done: a placeholder `Inter-Regular.ttf` is now bundled; replace with the real Inter font before production use.)
6. Review `handle_drift` logic to ensure it re-extracts from actual vendor files rather than accepting the drifted file as the new pristine. (Done.)
7. Implement a `usurface pause` or document how to disable the timer manually. (Done: `usurface pause`, `usurface resume`, and `status` paused indicator added.)
8. Consider making SDDM/QML paths configurable for non-Breeze themes.
9. Add at least one `@pytest.mark.integration` test skeleton, even if skipped by default.
10. Bump version to `0.1.0` when ready to tag.

---

## 12. Conclusion

`uniquesurface` is a well-architected, functionally complete Python CLI for managing Plasma 6 wallpapers across desktop, lock, and login surfaces. Its modular design, atomic I/O, manifest-based reversibility, and plugin-based providers are strengths. The codebase is ready for careful real-world testing, but it needs a quality pass (linting, formatting, type checking, dev dependency declarations) before it can be considered release-ready by professional standards. With the recommendations above addressed, it would be a robust and maintainable tool.

---

*Report generated by automated codebase investigation.*