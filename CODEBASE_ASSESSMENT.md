# trinity — Enterprise Linux Software Engineering Quality Assessment

**Date:** 2026-07-10
**Target system:** `trinity` — Unified KDE Plasma 6 surface-set manager
**Assessment lens:** Enterprise Linux software engineering best practices and patterns
**Auditor:** Automated senior-architect review (code-first; docs consulted but not trusted blindly)
**Key question:** *Investigate the codebase, identify bugs/smells/anti-patterns/inconsistencies, and assess quality in relation to established best practices and patterns within the enterprise Linux software engineering domain.*

---

## PART 1: Planning and Evidence Log

### Search Queries Executed (workspace)
- `[tool.ruff|[tool.mypy|coverage` in `pyproject.toml` → **2 matches** (only `[tool.hatch]`; **no ruff/mypy/coverage config blocks**)
- `signal\.|SIGTERM|SIGINT|atexit|KeyboardInterrupt` → **1 match** (CLI KeyboardInterrupt in excepthook only; **no signal handlers**)
- `subprocess\.run|Popen|check` → **12 matches** across 5 files (all use explicit argv, no `shell=True`)
- `fsync|os\.sync|fdatasync` → **16 matches** (atomic.py fsyncs files; **no directory fsync**)
- `umask|geteuid|getuid|setuid|privilege|drop_priv` → **14 matches** (sudo-aware patterns in 5 files)
- `Type=oneshot|ProtectSystem|NoNewPrivileges|SystemCallFilter` → **12 matches** but only `Type=oneshot` is in the actual unit template; **no systemd hardening directives**
- `TimeoutStartSec|TimeoutStopSec|WatchdogSec` → **3 matches** (all in comments; **not in the unit template**)
- `XDG_|platformdirs` → **91 matches** (pervasive XDG compliance)
- `except Exception|except :|BLE001` → **10 matches** (broad excepts in provider loading and CLI)
- `exit\(|sys\.exit` → exit codes: 0, 1, 2 used inconsistently across CLI commands

### Files Scanned (primary)
- `pyproject.toml`, `README.md`, `PLAN.md`, `CHANGELOG.md`, `.gitignore`, `docs/config-reference.md`, `docs/migration-from-shell.md`
- All 29 source files in `src/trinity/` (full read)
- `tests/conftest.py`, `test_graceful_failure.py`, `test_cli.py`, `test_backends_lock.py`, `test_systemd.py`, `test_orchestrator_drift.py`, `test_manifest.py`, `test_paths.py`
- Coverage report: `pytest --cov=trinity --cov-report=term-missing` → **75% overall**

### Found Discrepancies (code vs enterprise patterns)
1. **No systemd service hardening** — the generated `trinity-pull.service` lacks `ProtectSystem`, `ProtectHome`, `PrivateTmp`, `NoNewPrivileges`, `RestrictAddressFamilies`, `SystemCallFilter`, etc. These are standard hardening directives for enterprise systemd units.
2. **No explicit timeout in the service unit** — the code comments reference `TimeoutStopSec=90s` but the template doesn't set it. A hung `httpx` call could run indefinitely (the in-code subprocess timeouts are 10s, but the top-level `httpx` client timeout comes from config, default 30s — however, there's no systemd-level kill timer).
3. **No ruff/mypy configuration** — both tools run with defaults. Enterprise projects typically pin `target-version`, `line-length`, `select`/`ignore` rulesets, and mypy strictness in `pyproject.toml`.
4. **No CI pipeline** — no `.github/workflows/`, no `tox.ini`, no pre-commit hooks. Tests/lint/type-checking are manual-only.
5. **No `TimeoutStartSec` in the timer** — a `Type=oneshot` service with no `TimeoutStartSec` inherits systemd's default (90s), but this is implicit, not explicit.

### Strategic Hypotheses
The codebase demonstrates strong adherence to many enterprise Linux patterns (XDG compliance, atomic I/O, strict config validation, structured logging, sudo-aware privilege management) but has notable gaps in systemd service hardening, CI/CD, tool configuration, and signal handling. The overall quality is well above average for a solo-developer desktop tool and would meet enterprise standards with moderate effort in the hardening/CI areas.

---

## PART 2: Final Audit Report

### Executive Summary

**Verdict:** trinity is a high-quality CLI tool that adheres to most established enterprise Linux software engineering patterns: XDG Base Directory compliance, atomic file I/O with fsync, strict pydantic-validated TOML configuration, structured JSON logging for the systemd journal, sudo-aware privilege dropping, bounded append-only state, and comprehensive test isolation. The codebase scores **8.2/10** against enterprise Linux best practices. The most significant gaps are: (1) no systemd service hardening directives (`ProtectSystem`, `NoNewPrivileges`, etc.), (2) no CI/CD pipeline or pre-commit hooks, (3) no explicit ruff/mypy configuration, and (4) no SIGTERM/signal handling for clean shutdown under systemd. The single most important takeaway: the systemd unit that runs daily under a user timer should be hardened with sandboxing directives — this is the #1 enterprise Linux pattern missing from the codebase.

---

### 1. Technical Audit

#### 1.1 Component-by-Component Assessment (Enterprise Pattern Lens)

| Component | Score | Enterprise pattern evaluation |
|-----------|-------|-------------------------------|
| `paths.py` | 9/10 | ✅ Full XDG Base Directory spec compliance via `platformdirs`; ✅ sudo-aware home resolution; ✅ `$TRINITY_SHARED_DIR` env override. Minor: `last_wallpaper()`/`shared_wallpaper()` return `.jpg` while orchestrator uses dynamic extension. |
| `atomic.py` | 7/10 | ✅ tmp + fsync + `os.replace` pattern; ✅ EXDEV cross-filesystem fallback; ✅ sibling temp fallback; ⚠️ **no directory fsync after rename** (crash consistency gap); ⚠️ non-atomic `shutil.copy2` last-resort fallback (documented but loses atomicity). |
| `manifest.py` | 9/10 | ✅ `O_APPEND\|O_CREAT` atomic append; ✅ bounded history (200 entries) with compaction; ✅ snapshot dedup by SHA-256; ✅ corruption tolerance (skips bad lines); ✅ atomic log truncation. |
| `config.py` | 8/10 | ✅ `tomllib` (stdlib, safe parser); ✅ pydantic strict mode (`extra="forbid"`); ✅ sudo-aware `~` expansion; ⚠️ custom `_to_toml` serializer is fragile (no nested lists of dicts); ⚠️ no round-trip test. |
| `schema.py` | 9/10 | ✅ `extra="forbid"` catches typos; ✅ regex-validated fields (color, provider, font, clock); ✅ `model_validator` for schema version migration; ✅ legacy key stripping with warning. |
| `logging.py` | 8/10 | ✅ structlog JSON output to stdout (journal-captured); ✅ ISO UTC timestamps; ✅ log level configurable via CLI. ⚠️ `PrintLoggerFactory` writes to stdout, not stderr — convention is logs→stderr, data→stdout. ⚠️ no `journal` handler (relies on systemd's stdout→journal capture, which is fine but less explicit). |
| `cli.py` | 8/10 | ✅ Click groups with lazy imports; ✅ `standalone_mode=False` for excepthook; ✅ exit codes 0/1/2; ✅ `--dry-run` on apply; ⚠️ exit code 2 used for "file exists, refuse overwrite" but also for "missing provider" — inconsistent semantics; ⚠️ no `--help` customization; ⚠️ no `--quiet`/`--verbose` beyond `--log-level`. |
| `orchestrator.py` | 8/10 | ✅ deterministic pipeline order; ✅ per-backend error isolation (BackendError caught, others propagate); ✅ sudo ownership restoration; ✅ manifest compaction after success; ⚠️ compaction runs even after partial backend failures. |
| `backends/_kconfig.py` | 8/10 | ✅ explicit argv (no `shell=True`); ✅ per-call `timeout=10.0`; ✅ `check=True` on writes, `check=False` on reads; ✅ sudo drop with `XDG_RUNTIME_DIR` + `DBUS_SESSION_BUS_ADDRESS`; ⚠️ `ensure_tool` error message names wrong package. |
| `backends/desktop.py` | 8/10 | ✅ correct nested containment group discovery; ✅ live D-Bus apply via `evaluateScript`; ⚠️ containment parser is a hand-rolled state machine (could use `kreadconfig6` or an INI parser); ⚠️ `evaluateScript` JS injection is escaped but the escape is minimal (backslash + single quote only). |
| `backends/lock.py` | 9/10 | ✅ correct `[Greeter][Wallpaper][org.kde.image][General]` path; ✅ live reload via `org.kde.screensaver.configure`; ✅ manifest-tracked. |
| `backends/login.py` | 8/10 | ✅ regex-based `theme.conf` editing; ✅ writability pre-check; ✅ `login_surface_needs_root()` for CLI pre-flight warning. ⚠️ `_set_key` doesn't escape regex special chars in the replacement value (low risk on Linux paths). |
| `providers/__init__.py` | 8/10 | ✅ pluggy hookspec/hookimpl pattern; ✅ entry-point discovery with error isolation; ✅ structural `Protocol`; ⚠️ `SourceOptions` uses `extra="allow"` — no validation of unknown keys (typos silently ignored). |
| `providers/builtin/bing.py` | 8/10 | ✅ streaming download with size cap; ✅ `follow_redirects=True`; ✅ URL validation; ✅ single client with connection reuse. ⚠️ hardcoded User-Agent (could be configurable); ⚠️ no retry/backoff on transient failures. |
| `providers/builtin/file.py` | 9/10 | ✅ path-traversal protection via allow-listed roots; ✅ symlink resolution; ✅ size cap (100 MiB). |
| `providers/builtin/solid.py` | 9/10 | ✅ Pillow composite for gradient (C-speed); ✅ dimension cap (7680px); ✅ quality clamping. |
| `systemd/writer.py` | 6/10 | ✅ inline templates (no Jinja2 dep); ✅ `Persistent=true` for missed-run catchup; ✅ `RandomizedDelaySec` for fleet de-thundering; ❌ **no hardening directives**; ❌ **no `TimeoutStartSec`**; ⚠️ `WorkingDirectory=cwd` captures the install-time CWD (could be `/root` under sudo). |
| `theme/drift.py` | 8/10 | ✅ SHA-256-based drift detection; ✅ consent-gated adoption; ✅ backup dedup; ✅ no-pristine vs drift distinction. |
| `theme/extract.py` | 9/10 | ✅ atomic writes; ✅ sentinel stripping before storing pristine. |
| `theme/qml_patch.py` | 8/10 | ✅ sentinel-based patching (valid QML comments); ✅ in-place property value replacement; ⚠️ `_WAKE_GUARD_BLOCK_RE` line-count coupling (brittle). |
| `theme/font_install.py` | 7/10 | ✅ system-wide + user-local fallback; ✅ `fc-cache -f` after install; ✅ `fc-match` exact family match; ⚠️ 45% test coverage (lowest in the codebase); ⚠️ `timeout=120.0` for fc-cache is very long. |
| `tests/` | 7/10 | ✅ 114 tests, 1.1s, XDG isolation; ✅ `respx` for HTTP mocking; ✅ `monkeypatch` for subprocess; ⚠️ 75% coverage overall; ⚠️ `cli.py` at 47% (many commands untested); ⚠️ `atomic.py` at 34% (fallback paths untested); ⚠️ no integration test actually runs `apply` end-to-end against a real (mocked) Plasma session. |

#### 1.2 Security Deep-Dive

**Threat model:** trinity runs as a user-level CLI and systemd timer. It shells out to `kwriteconfig6`/`qdbus6`, makes HTTP requests (Bing), writes to user config dirs and (with sudo) system dirs, and patches vendor QML files. The attack surface is: (1) the HTTP provider (Bing), (2) the config file (TOML), (3) the provider plugin entry points, (4) the QML patching path.

| Finding | Severity | Likelihood | Impact | Assessment |
|---------|----------|------------|--------|------------|
| **No systemd service hardening** | High | Likely | Major | The `trinity-pull.service` runs daily with full user privileges, network access, and filesystem write access. Enterprise systemd units should use `ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp=true`, `NoNewPrivileges=true`, `RestrictAddressFamilies=AF_INET AF_INET6`, `SystemCallFilter=@system-service`, `ReadWritePaths=%h/.local/state/trinity /usr/local/share/wallpapers`. Without these, a compromised provider response or a bug can write anywhere in the user's home. |
| **Provider plugin supply chain** | Medium | Possible | Major | Third-party providers loaded via `importlib.metadata.entry_points()` run arbitrary Python as the invoking user. The code logs a warning in the README but doesn't sandbox or restrict plugin imports. Enterprise pattern: at minimum, log which plugins are loaded and provide an explicit allow-list in config. |
| **`evaluateScript` JS injection** | Low | Rare | Moderate | The wallpaper URI is escaped for single quotes and backslashes before insertion into a JS string literal inside a `qdbus6` call. The URI comes from `wallpaper.resolve().as_uri()` which produces a `file://` URL — controlled input. However, the escape doesn't handle all JS string-literal edge cases (e.g. `\0`, `\n` in a path). A pathological filename could inject JS. Low likelihood (filenames are trinity-controlled) but the escape should be more thorough. |
| **`SourceOptions` extra="allow"** | Low | Likely | Minor | Any typo in `[surface.source.options]` (e.g. `resoultion` instead of `resolution`) is silently accepted by the schema and ignored by the provider (which uses `.get()` with defaults). Enterprise pattern: validate options against a provider-specific schema and raise on unknown keys. |
| **Path traversal (file provider)** | Low | Unlikely | Minor | Protected by allow-listed roots + `Path.resolve()` symlink resolution. TOCTOU between check and read is theoretically exploitable but impractical on a single-user desktop. |
| **No `shell=True`** | — | — | — | ✅ All subprocess calls use explicit argv lists. No command injection vector. |
| **Safe deserialization** | — | — | — | ✅ `tomllib` and `json.loads` only. No `pickle`/`eval`/`exec`. |
| **EXIF stripping** | — | — | — | ✅ `verify_image` re-encodes all images, stripping metadata. Privacy improvement. |

#### 1.3 Performance & Resource Efficiency

| Area | Measurement | Enterprise pattern | Assessment |
|------|-------------|-------------------|------------|
| Test suite | 114 tests, 1.1s | Fast feedback loop | ✅ Excellent |
| Import time | Lazy imports in CLI commands | Fast startup | ✅ Click commands defer heavy imports |
| HTTP (Bing) | Single `httpx.Client`, streaming, 50 MiB cap | Connection reuse, bounded memory | ✅ Fixed in prior audit |
| Image processing | Pillow composite for gradients (~50× faster than loop) | C-level operations | ✅ Fixed in prior audit |
| Manifest append | O(1) via `O_APPEND` | Kernel-atomic append | ✅ No read-modify-write |
| Manifest compaction | O(N), bounded to 200 entries | Bounded state growth | ✅ Cannot grow unbounded |
| Snapshot storage | Dedup by SHA-256 | Content-addressed storage | ✅ No duplicate snapshots |
| Coverage | 75% overall | Enterprise: 80%+ | ⚠️ Below enterprise threshold; `cli.py` (47%) and `atomic.py` (34%) drag it down |
| Memory (solid provider) | Dimension-capped at 7680px | Bounded allocation | ✅ Fixed in prior audit |

#### 1.4 Codebase Quality & Maintainability

| Criterion | Score | Evidence |
|-----------|-------|----------|
| **Language use** | 9/10 | Python 3.12+, `from __future__ import annotations` throughout, type hints on all public functions, `dataclass(frozen=True)` for value objects, `Protocol` for structural typing |
| **Linting** | 7/10 | `ruff check` clean but **no `[tool.ruff]` config** — runs with defaults. Enterprise: pin `target-version`, `line-length`, explicit rule selection. |
| **Type checking** | 7/10 | `mypy src` clean but **no `[tool.mypy]` config** — runs with defaults. Enterprise: `strict = true`, `disallow_untyped_defs = true`, `warn_return_any = true`. |
| **Testing** | 7/10 | 114 tests, 1.1s, XDG isolation, HTTP mocking. ⚠️ 75% coverage; ⚠️ no CI; ⚠️ no integration tests; ⚠️ `cli.py` at 47%. |
| **Formatting** | 8/10 | Code is consistently formatted (ruff format clean). No explicit `line-length` config but appears to follow 88. |
| **Error handling** | 8/10 | `CLIError` with hint pattern; `BackendError` with hint; per-backend isolation; corruption-tolerant manifest. ⚠️ broad `except Exception` in 5 places (provider loading, CLI fallbacks). |
| **Exit codes** | 6/10 | Uses 0 (success), 1 (generic error), 2 (usage/refusal). ⚠️ Inconsistent: `config_init` uses 2 for "file exists"; `provider_info` uses 2 for "not found"; `config_validate` uses 1 for invalid; `doctor` uses 1 for problems. Enterprise pattern: follow `sysexits.h` (EX_USAGE=64, EX_DATAERR=65, EX_NOINPUT=66, EX_UNAVAILABLE=69) or at least document the convention. |
| **Signal handling** | 4/10 | Only `KeyboardInterrupt` in the excepthook. ❌ No `SIGTERM` handler — under systemd, `SIGTERM` is sent on `systemctl stop` and the process is killed after `TimeoutStopSec`. For a `Type=oneshot` service this is less critical (no long-running state to flush), but the HTTP download could be interrupted mid-stream. Enterprise pattern: register a `SIGTERM` handler that sets a flag and exits cleanly. |
| **Documentation** | 8/10 | Docstrings on all modules and public functions; `docs/config-reference.md` and `docs/migration-from-shell.md` are accurate; `PLAN.md` is thorough. ⚠️ No `CONTRIBUTING.md`; ⚠️ no API reference. |
| **Dependency management** | 8/10 | `uv` with lockfile; pinned version ranges (`>=X,<Y`); dev/test deps in separate groups. ⚠️ No `dependabot`/`renovate` config. |
| **Packaging** | 8/10 | hatchling backend; `src/` layout; wheel + sdist; console script entry point. ⚠️ No `MANIFEST.in` (hatchling handles this); ⚠️ no PyPI publication config. |

---

### 2. Unorthodox Feature Spotlight

1. **Sentinel-based QML patching with drift detection**
   - *What:* Replaces values of existing `readonly property string` declarations in vendor QML files, wrapping changes in `/* @trinity:start */ ... /* @trinity:end */` comment markers for tracking.
   - *Why unusual:* Most Linux desktop tools either don't patch QML or do so irreversibly. The sentinel approach keeps QML parseable (comments are valid QML) while enabling drift detection and byte-level restore.
   - *Alternative applications:* Could be generalized into a "safe vendor-file patcher" library for any INI/QML/conf file that needs reversible, drift-aware edits. Useful for KDE theming, GNOME shell extensions, or any system that patches vendor-shipped config files.

2. **Append-only manifest with SHA-256 snapshot dedup**
   - *What:* Every file write is recorded as a JSONL entry with a snapshot of the previous content, stored by SHA-256. `restore` replays inverse operations newest-first.
   - *Why unusual:* This is event-sourcing for a desktop wallpaper tool. Most desktop tools have no undo, or at best a single backup file. The content-addressed snapshot dedup is a storage-efficiency pattern from backup systems.
   - *Alternative applications:* Could be extracted as a generic `tracked-fs` library for any CLI tool that modifies system config files and needs reversible, auditable changes. Useful for package managers, config management tools, or any "mutate system files safely" use case.

3. **Sudo-aware ownership restoration**
   - *What:* After a `sudo trinity apply`, the shared wallpaper file is chowned back to the invoking user (`$SUDO_USER`) so the daily user-mode systemd timer can still overwrite it.
   - *Why unusual:* Most CLI tools that support sudo either leave files root-owned (breaking subsequent user-mode runs) or require the user to manually fix permissions. The automatic restoration via `SUDO_USER`/`SUDO_UID` env vars is a thoughtful operational pattern.
   - *Alternative applications:* Any tool that has both a "sudo one-shot" and a "user-mode recurring" mode (e.g. cert managers, log rotators, backup tools) could benefit from this pattern.

4. **D-Bus sudo-drop with session bus forwarding**
   - *What:* When run via sudo, D-Bus calls (`qdbus6`) are wrapped in `sudo -u $SUDO_USER env XDG_RUNTIME_DIR=/run/user/$SUDO_UID DBUS_SESSION_BUS_ADDRESS=unix:path=.../bus` so they reach the user's session bus, not root's.
   - *Why unusual:* D-Bus under sudo is a notoriously tricky problem. Most tools either don't support sudo+D-Bus or require the user to manually set env vars. The automatic session-bus forwarding is correct and well-documented.
   - *Alternative applications:* Any tool that needs to make D-Bus calls to a user's session from a sudo context (e.g. notification senders, KDE config tools, GNOME settings tools).

5. **No-pristine vs drift distinction**
   - *What:* The drift detector distinguishes "no stored pristine template" (trinity install never ran) from "vendor file changed since last install" (actual drift). The former gets a "run install first" hint; the latter gets a backup + consent-gated adoption.
   - *Why unusual:* Most drift detectors (Puppet, Ansible) conflate "no baseline" with "drifted from baseline." The distinction prevents false-positive drift reports and unnecessary backup file accumulation.
   - *Alternative applications:* Any config-management tool that needs to distinguish "never managed" from "managed but drifted."

---

### 3. Competitive Landscape & Market Positioning

#### 3.1 Peer System Comparison

| System | Strengths | Weaknesses | trinity differentiator |
|--------|-----------|------------|----------------------|
| `variety` | Mature, many sources, GUI | Desktop only, no undo, no QML, no systemd | Three-surface sync + reversible + systemd |
| `nitrogen` | Lightweight, fast | Desktop only, no automation | Provider registry + systemd timer |
| `wallpaper-engine-linux` | Animated wallpapers | Steam dependency, no lock/login | Native Plasma 6, no Steam |
| `kwriteconfig6` + cron | Simple, no deps | No undo, no automation, manual | Full pipeline + manifest undo + drift detection |
| `sddm-theme-conf` manual edit | Direct control | Irreversible, no validation | Safe patching + drift detection |
| `KDE Plasma settings` (GUI) | Official, stable | Desktop only, no automation | CLI-first + three-surface + systemd |

#### 3.2 Market Gap Analysis
trinity occupies a unique niche: CLI-first, reversible, systemd-automated, three-surface wallpaper management for KDE Plasma 6. No other tool covers all three surfaces (desktop + lock + login) with undo capability and drift detection. The target user is a KDE Plasma 6 power user / Linux enthusiast who wants a cohesive look without manual config editing or risking a bricked login screen.

#### 3.3 Pricing/Packaging Suggestions
Current: PolyForm Noncommercial 1.0.0. This is appropriate for a personal/niche tool. For broader adoption:
- **Option A:** Keep PolyForm Noncommercial; distribute via `uv tool install` and AUR (`yay -S trinity-wallpaper`).
- **Option B:** Dual-license as MIT for community adoption + commercial license for enterprise KDE deployments (kiosk systems, corporate Plasma workstations).
- **Option C:** Package as a `.deb`/`.rpm` for distro inclusion (would require switching to a more permissive license).

No SaaS/pricing model applies — this is a local CLI tool.

---

### 4. Academic & Research Alignment

#### 4.1 Relevant Papers
No direct arXiv papers apply (desktop wallpaper management is not a research topic). The closest relevant patterns from the systems/software engineering literature:

- **Atomic file writes** — The tmp+fsync+rename pattern is standard crash-consistency technique (ext4 journaling, SQLite WAL, dpkg's deferred-write pattern). trinity implements this correctly but misses the directory fsync step documented in the POSIX `rename(2)` man page and the ext4 data=journal literature.
- **Event sourcing / CQRS** — The append-only manifest is a classic event-sourced undo log, related to the event-sourcing pattern in distributed systems (e.g. Kafka, EventStoreDB). The snapshot dedup is analogous to content-addressed storage (Git, Casandra).
- **Configuration drift detection** — Analogous to Puppet's `puppet agent --noop` idempotency check and Ansible's `--check` mode, but applied to vendor QML files rather than managed config files.

#### 4.2 Novelty Assessment
The sentinel-based QML patching + drift detection pattern is the most novel element. It could be published as a KDE-related conference talk (Akademy) or a Linux Magazine article. It's not patentable (it's a file-patching technique), but it could become an influential design pattern for KDE theming tools. The append-only manifest with snapshot dedup is a nice application of event-sourcing to desktop tooling but is not novel in the academic sense.

---

### 5. Actionable Roadmap

#### Quick Wins (S = hours)

| # | Description | Effort | Impact | Risk |
|---|-------------|--------|--------|------|
| Q1 | **🔑 Add systemd service hardening directives** to `_SERVICE_TEMPLATE`: `ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp=true`, `NoNewPrivileges=true`, `RestrictAddressFamilies=AF_INET AF_INET6`, `SystemCallFilter=@system-service`, `ReadWritePaths=%h/.local/state/trinity %h/.config/trinity /usr/local/share/wallpapers`, `TimeoutStartSec=120` | S | High | High → Medium |
| Q2 | **🔑 Add `[tool.ruff]` config** to `pyproject.toml`: `target-version = "py312"`, `line-length = 88`, `select = ["E", "W", "F", "I", "UP", "B", "SIM", "C4"]` | S | Medium | Low |
| Q3 | **🔑 Add `[tool.mypy]` config**: `strict = true`, `warn_return_any = true`, `disallow_untyped_defs = true` | S | Medium | Low |
| Q4 | **Add `TimeoutStartSec=120`** to the service template (explicit, not inherited) | S | Medium | Medium |
| Q5 | **Fix `WorkingDirectory`** in service template — use `~` (home) instead of the install-time CWD (which could be `/root` under sudo) | S | Medium | Medium |
| Q6 | **Add `.github/workflows/ci.yml`** running `ruff check`, `mypy src`, `pytest -q` on push/PR | S | High | Low |
| Q7 | **Add `CONTRIBUTING.md`** with dev setup, test commands, code style | S | Low | Low |

#### Medium Effort (M = days)

| # | Description | Effort | Impact | Risk |
|---|-------------|--------|--------|------|
| M1 | **🔑 Add SIGTERM handler** that sets a shutdown flag; check it between backends in the apply loop so a `systemctl stop` during a daily run exits cleanly | M | Medium | Medium |
| M2 | **Add directory fsync** after `os.replace` in `atomic.py` for full POSIX crash consistency | M | Medium | Medium |
| M3 | **Add provider option validation** — each provider declares a pydantic model for its options; unknown keys raise at config load time | M | Medium | Low |
| M4 | **Improve CLI test coverage** from 47% to 80%+ — test `config init/show/validate`, `provider list/info`, `doctor`, `migrate-from-shell`, `install/uninstall/pause/resume` | M | Medium | Low |
| M5 | **Improve atomic.py test coverage** from 34% to 80%+ — test EXDEV fallback, sibling temp, direct overwrite, error paths | M | Medium | Low |
| M6 | **Add `pre-commit` hooks** for ruff + mypy + pytest-fast | M | Low | Low |
| M7 | **Add exit code convention** to `CONTRIBUTING.md` and align all `sys.exit()` calls (e.g. 0=success, 1=runtime error, 2=usage error, 3=config error) | M | Low | Low |

#### Long-Term Bets (L = weeks)

| # | Description | Effort | Impact | Risk |
|---|-------------|--------|--------|------|
| L1 | **Package as `.deb`/`.rpm`** with a systemd unit, font, and postinst script for distro inclusion | L | High | Low |
| L2 | **Add integration test harness** that spins up a headless Plasma session (or a mock `kscreenlocker_greet`) and verifies the full apply pipeline end-to-end | L | High | Medium |
| L3 | **Generalize the sentinel-patching + drift-detection** into a standalone library (`vendor-patch`) reusable by other KDE/GNOME theming tools | L | Medium | Low |
| L4 | **Add APT/DNF repository** with signed packages for automated updates | L | High | Low |

*🔑 = directly addresses the key_question (enterprise Linux best practices).*

---

### 6. Supplementary Documentation Verification

#### 6.1 Feature-Implementation Gap Analysis

| Feature | Claimed in | Found in code | Score | Notes |
|---------|-----------|---------------|-------|-------|
| Three-surface sync (desktop/lock/login) | README, PLAN | ✅ `orchestrator.py` | 10/10 | All three implemented and tested |
| Atomic rollbacks via manifest | README, PLAN | ✅ `manifest.py` | 10/10 | Append-only JSONL + snapshot restore + bounded compaction |
| Safe QML patching with drift detection | README, PLAN | ✅ `qml_patch.py`, `drift.py` | 9/10 | Sentinel-based; adopt_drift bug fixed in prior audit |
| Provider registry (pluggy) | README, PLAN | ✅ `providers/__init__.py` | 9/10 | Built-ins + entry-point loading; no option validation |
| Systemd daily timer | README, PLAN | ✅ `systemd/writer.py` | 7/10 | Works but **lacks hardening directives** |
| Strict pydantic config | README, PLAN | ✅ `schema.py` | 10/10 | `extra="forbid"`, regex validation, legacy key stripping |
| Atomic I/O (fsync) | README, PLAN | ✅ `atomic.py` | 8/10 | File fsync yes; **directory fsync no** |
| Structured JSON logging | PLAN | ✅ `logging.py` | 8/10 | structlog JSON to stdout; **logs to stdout not stderr** |
| `trinity doctor` health check | README | ✅ `cli.py` | 10/10 | Checks config, manifest, shared dir, font, timer, drift |
| `trinity migrate-from-shell` | README, docs | ✅ `cli.py` | 9/10 | Detects legacy script + timer; writes starter config |
| Font install (Inter) | README, PLAN | ✅ `font_install.py` | 8/10 | System-wide + user-local fallback; 45% test coverage |
| `trinity pause`/`resume` | README | ✅ `cli.py`, `systemd/writer.py` | 10/10 | Runtime mask/unmask; resume checks enable result |

#### 6.2 Version Drift Analysis
- `src/trinity/__init__.py`: `__version__ = "0.1.0"`
- `pyproject.toml`: `version = "0.1.0"`
- `CHANGELOG.md`: `[Unreleased]` — no released version yet
- **Consistent.** No version drift. The `[Unreleased]` changelog is appropriate for a pre-release tool.

---

### 7. Closing Remarks

trinity is a well-crafted tool that demonstrates a genuine understanding of Linux desktop engineering. The XDG compliance, atomic I/O, sudo-aware privilege management, structured logging, append-only manifest with dedup, and drift-detection-with-consent patterns are all things I'd expect from a senior Linux engineer, not a weekend project. The code is clean, the tests are fast, and the documentation is honest about limitations.

The gaps I've identified are the kind that matter in an enterprise context but are easy to miss in solo development: systemd service hardening (the #1 fix — adding `ProtectSystem`, `NoNewPrivileges`, etc. to the unit template would take 15 minutes and dramatically reduce the blast radius of a compromised provider response), CI/CD automation (no `.github/workflows/` means quality gates are only enforced by discipline), tool configuration (ruff and mypy run with defaults rather than pinned strictness), and signal handling (a `SIGTERM` handler for clean shutdown under systemd). None of these are architectural problems — they're operational hygiene.

The codebase is ready for its next phase: distro packaging, CI, and hardening. With the Quick Wins implemented (especially Q1 and Q6), it would meet the bar for inclusion in a community Linux distribution.

---

### 8. Template Self-Audit & Feedback

**What worked well:** The key_question's emphasis on "enterprise Linux software engineering domain" gave the audit a clear lens — instead of generic code-quality findings, I evaluated against specific patterns (XDG, FHS, systemd hardening, POSIX atomicity, sysexits.h, structlog/journald). The component table format worked well for pattern-by-pattern assessment.

**What was unclear:** The template's academic/market/competitive sections are poorly suited for a niche desktop CLI tool. The "arXiv papers" and "pricing/packaging" sections feel forced for a non-AI, non-SaaS project. A "domain-specific best practices" section would be more valuable than academic alignment for systems software.

**Suggested improvements for v2.2:**
1. Add a "domain pattern checklist" section that enumerates the expected patterns for the target domain (e.g. for Linux CLI tools: XDG, FHS, systemd, atomic I/O, signal handling, exit codes, i18n, man pages). This would make the assessment more systematic and less dependent on the auditor's domain knowledge.
2. Make the academic/market sections optional based on the system type. For systems software, replace with "standards compliance" (POSIX, FHS, XDG, freedesktop.org).
3. Add a "CI/CD assessment" criterion — the current template doesn't mention continuous integration, which is a core enterprise practice.