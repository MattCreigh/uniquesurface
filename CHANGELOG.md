# Changelog

All notable changes to `trinity` are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/) and this project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- **GUI runs in a native window instead of the browser.** `trinity-gui` now renders its UI in a native WebKitGTK window (via `webview_go`) rather than shelling out to the default browser, so it no longer opens as a Chrome tab. It still falls back to the system browser on a headless/no-display session. All of the 0.3.1 hardening (bearer token, `Host` check, POST-only, timeouts) is unchanged. `make install` installs the binary to `~/.local/bin` and a KDE `.desktop` launcher to `~/.local/share/applications`. Build deps: `golang-go`, `libwebkit2gtk-4.1-dev`, `libgtk-3-dev`; a pkg-config shim in `gui/packaging/pkgconfig` maps webkit2gtk-4.0 → 4.1 for Ubuntu 24.04 / KDE Neon.

## [0.3.1] — 2026-07-23

Golden-master remediation release: closes the defects found in the
post-0.3.0 audit. No new features; behaviour-compatible with 0.3.0
except where a documented feature was previously inert.

### Fixed

- **HIGH-1 — clock-position patching is now wired into the apply pipeline.** `apply_clock_position_tokens` was implemented, schema-validated, and unit-tested in 0.3.0 but never called by the orchestrator, so `[surface.theme_tokens.clock_position]` did nothing. It now runs during `apply` (per-target clock id), routed through the manifest and the qmllint fail-closed gate, and respects `--dry-run`.
- **HIGH-2 — `trinity cycle` no longer crashes on non-indexed providers.** The temporal offset is now injected only for providers whose options schema declares `index`; `solid`/`file`/`json-api` cycle requests raise a clean `CLIError` (exit 2) instead of an unhandled Pydantic `ValidationError`. `cli.cycle` and `cli.apply` wrap the pipeline so provider/backend failures surface as graceful errors, never as "unexpected error".
- **MEDIUM-3 — Go GUI control plane hardened.** `/api/*` endpoints enforce POST (405 otherwise), validate the `Host` header (421 on mismatch), require a per-process bearer token, and the server sets `ReadHeaderTimeout`/`ReadTimeout`/`WriteTimeout`/`IdleTimeout`.
- **MEDIUM-4 — compiled binary removed from version control.** `gui/trinity-gui` is untracked and `.gitignore`d, along with `*.deb`, `/dist/`, and build artifacts.
- **LOW-5a** — removed the redundant, format-mismatched `refresh_state.cycle_token`; the offset token has a single definition in the orchestrator.
- **LOW-5b** — dropped the stale hardcoded version from the `logging_setup` docstring.
- **LOW-5c** — `apply` computes the lock `user_dir` via `expand_behaviour_paths`, so `$VAR` in `user_dir` resolves consistently for lock acquisition.
- **LOW-5d** — the NetworkManager dispatcher validates the target username (`^[a-z_][a-z0-9_-]*$`) before templating it into the script.
- **LOW-5e** — `verify_image` saves and restores `Image.MAX_IMAGE_PIXELS` instead of mutating the PIL global permanently.
- **LOW-5f** — `manifest.restore` writes snapshots back with mode `0o644` and, under sudo, restores the invoking user's ownership.

## [0.3.0] — 2026-07-22

### Added — Features

- **`trinity cycle` — temporal cyclical provisioning.** Cycle retrospectively through the past 7 days of Bing/RSS wallpapers without breaking the hourly `--if-changed` deterministic probe. `trinity cycle --offset N` fetches the image from N days ago; `trinity cycle` (no offset) increments the current offset by 1 (mod 7). The base `config.toml` is never mutated — the active temporal offset is persisted in `refresh_state.json`. The compound `--if-changed` token combines the provider fingerprint + offset so the hourly timer does not clobber a manual cycle.

- **Clock position QML patching.** `[surface.theme_tokens.clock_position]` allows repositioning the clock on SDDM and lock-screen surfaces. Layout-managed clocks use `Layout.alignment`; independent items use `anchors` or explicit `x`/`y` coordinates. The patcher detects the enclosing container type automatically and preserves existing dynamic bindings (`visible`, `opacity`). Alignment tokens: `top`, `bottom`, `left`, `right`, `center`, `top_left`, `top_right`, `bottom_left`, `bottom_right`.

- **RTC wake + NetworkManager dispatcher (opt-in).** `trinity install --wake-network` installs a wake-enabled systemd timer (`WakeSystem=true`) and a NetworkManager dispatcher script that runs `trinity apply --if-changed` when Wi-Fi reconnects. Requires root. The dispatcher is non-blocking (`&`), filters on `up` events only, and runs as the invoking user. RTC wakealarm availability is checked and warned if missing.

- **Go/Wails v3 GUI foundation.** A minimal `gui/` directory with a Go HTTP server that wraps `trinity` CLI commands. Parses `manifest.jsonl` for history, calls `trinity apply`/`cycle`/`status`/`restore`/`doctor` via `os/exec` (never writes state files directly). Build: `cd gui && go build -o trinity-gui .`

### Changed

- `RefreshState` gained a `temporal_offset` field (backward-compatible, defaults to 0).
- `apply_to_surfaces` gained a `temporal_offset` parameter that injects a day-offset into provider options for cyclical provisioning.
- `systemd.install()` gained a `wake_system` parameter.
- `trinity install` gained a `--wake-network` flag.
- `ThemeTokens` gained a `clock_position` field (backward-compatible, defaults to disabled).

### Fixed

- **CI workflow action versions corrected.** The workflows referenced non-existent versions (`actions/checkout@v7`, `actions/upload-artifact@v7`, `astral-sh/setup-uv@v7`). Pinned to real current versions: `actions/checkout@v4`, `actions/upload-artifact@v4`, `astral-sh/setup-uv@v5`.
- **Unknown provider no longer crashes with a traceback.** `validate_provider_options`, `fetch_from_source`, and `probe_from_source` now convert the `KeyError` from `get_provider` into a clear `ValueError`/`ProviderError` with the message `"unknown provider '<name>'; run 'trinity provider list'"`.
- **SSRF pre-flight rejects mixed DNS records.** `_resolve_safely` previously returned the first safe IP and only rejected when *no* address was safe. A hostname resolving to both a private and a public address (DNS rebinding) is now rejected — if *any* resolved address is private/loopback/link-local/reserved/multicast/unspecified, the request is refused.
- **Decompression-bomb guard added.** `verify_image` now sets `Image.MAX_IMAGE_PIXELS = 50_000_000` (50 MP) before opening and catches `DecompressionBombError`/`DecompressionBombWarning`, raising `ProviderError("image exceeds safe pixel limit")` instead of crashing the hourly timer with an OOM.
- **qmllint fail-closed gate no longer silently bypassed.** `lint_file` returned `ok=True` when no working qmllint was found, silently accepting every QML patch. It now returns `ok=False` with a helpful stderr message. The orchestrator reverts the patch unless the new `surface.theme_tokens.skip_qmllint` config flag is explicitly set to `true`.
- **Inter-process lock around apply.** Two concurrent `trinity apply` runs could race on the manifest and shared files. A `fcntl.flock` lockfile at `<user_dir>/lock` is now acquired during non-dry-run applies. The lock is best-effort: if `fcntl` is unavailable or the lockfile cannot be created, the apply proceeds with a warning.
- **Manifest restore is now transactional.** `restore` previously applied entries newest-first and raised `FileNotFoundError` on a missing snapshot mid-rollback, leaving previously reverted entries in a partially rolled-back state. It now pre-validates that every referenced snapshot exists *before* applying any entry, raising upfront with a clear message naming the missing snapshot.
- **SDDM fork is now atomic.** `fork_breeze_theme` deleted the existing fork with `shutil.rmtree` before rebuilding. If the copy failed, the SDDM theme directory was left missing. The fork is now built in a staging directory (`<name>.new`) and atomically swapped via rename. If the copy fails, the old fork survives intact.
- **pkexec/sudo inconsistency fixed.** `_have_pkexec()` checked for both `pkexec` and `sudo`, but `_restart_display_manager` only used `sudo -n`. When run as a non-root user, `pkexec` is now preferred (non-interactive) when available, falling back to `sudo -n`.
- **Password character validation.** `Fonts.password_character` now rejects control characters (`\x00`–`\x1F`, `\x7F`), newlines, tabs, double quotes, and backslashes that would break generated QML. The existing `min_length=1, max_length=4` constraints are preserved.
- **Atomic write fallback preserves exception context.** `_direct_overwrite` previously suppressed the original exception with `from None`. It now chains the exception with `from exc` so the root cause is visible in tracebacks.

### Changed

- **Snapshot disk budget added.** The manifest retains 200 entries; with 50 MiB wallpapers, snapshots could reach 10 GiB. An additional retention rule prunes unreferenced snapshots older than 30 days, or caps total snapshot directory size at 500 MiB, whichever is more restrictive. The 200-entry count remains as a secondary bound.
- **Font warning only shown when theme tokens are enabled.** `trinity apply` previously warned about a missing font family even when `theme_tokens.enabled = false`. Tier-1 (wallpaper-only) users no longer see the irrelevant fontconfig warning.
- **Descriptor/plasma detection deferred from import time.** The module-level regex constants in `qml_patch.py` were resolved at import time, calling `detect_plasma_version()` (which may shell out to `plasmashell --version`). They are now initialized with hardcoded fallback patterns at import time and upgraded lazily on first use via `_get_pattern()`, deferring version detection until the first actual patch operation.
- **`trinity restore --dry-run`** added. Previews restore operations without writing anything.

### Internal

- Test count grew from 348 to 398. New regression tests cover: mixed DNS SSRF, all-public DNS acceptance, IPv4-mapped IPv6 private rejection, unknown provider ValueError/ProviderError, decompression bomb rejection, missing snapshot restore abort, qmllint missing fail-closed, qmllint skip_qmllint opt-out, concurrent apply lock, password character validation, SDDM fork atomic swap, snapshot age/size pruning, and deferred descriptor resolution.
- Coverage: 84.50% → 85.12%.

## [0.2.6] — 2026-07-18

### Fixed

- **`apply --dry-run` no longer touches the filesystem.** The pipeline created `user_dir` and `shared_dir` (default `/usr/local/share/wallpapers`) before reaching the dry-run branch — a real mutation on a planning run, and the cause of the CI failure on v0.2.5 (`PermissionError` on GitHub runners, where `/usr/local` is not writable). The mkdirs and the shared-dir writability pre-flight now run only on real applies.
- **The qmllint gate no longer trusts a broken qtchooser shim.** `qmllint_available()` accepted any `qmllint` on PATH; on Debian-family systems that name can be a Qt 5 qtchooser shim that fails *every* invocation when only Qt 6 is installed (`could not exec '/usr/lib/qt5/bin/qmllint'`), so the fail-closed gate silently reverted every QML patch — font tokens never survived an `apply` on such hosts. Candidates (`qmllint6` first, then `qmllint`) are now probed with `--version` and only a binary that actually runs is used.

### Changed

- **Repository renamed** from `MattCreigh/uniquesurface` to `MattCreigh/trinity_background_manager`; project URLs, README badges, and the rss provider User-Agent updated (GitHub redirects the old name).
- **Dependency upper bounds widened** — `Pillow<13`, `structlog<27`, `packaging<27` — validated against Pillow 12.3, structlog 26.1, and packaging 26.2 on Python 3.14 by the new hermetic Nix build's test run.

### Added

- **Nix flake.** `nix build` / `nix run` build and run trinity (hatchling backend, dependencies from nixpkgs) with the full pytest suite executed in the sandboxed check phase; `nix develop` provides a `python` + `uv` shell; `nix flake check` wires the package as a check. README documents the entry points.

### Internal

- **Test isolation:** three orchestrator tests configured the `bing` provider without stubbing the fetch and silently hit the live Bing API on every CI/dev run (exposed by the no-network Nix sandbox); they now stub `fetch_wallpaper`/`verify_image`. The `python -m trinity --version` subprocess test preserves `PYTHONPATH` so it works where the package is exposed via env rather than site-packages. New regression tests cover the broken-shim and qt6-name-preference qmllint behaviour.

## [0.2.5] — 2026-07-13

### Changed

- **CLI exit codes follow BSD `sysexits.h`.** All `sys.exit(N)` calls in `cli.py` are now named constants in the new `trinity.exit_codes` module: `EXIT_ERROR=1`, `EXIT_USAGE=2`, `EXIT_DATAERR=65`, `EXIT_NOINPUT=66`, `EXIT_CANTCREAT=73`. `trinity install` without a config and without `--yes` now exits with `EXIT_USAGE` (was an opaque `1`); `trinity provider info <unknown>` exits with `EXIT_NOINPUT` (was `2`, ambiguous with usage errors); `trinity config init` refusing to overwrite exits with `EXIT_CANTCREAT` (was `2`).
- **Brace-balanced wake-guard removal.** The QML patcher previously removed the inserted wake-keypress guard by matching exactly 3 lines after the marker comment — fragile against upstream reformatting. The removal now walks brace pairs to find the closing `}`, so an extra blank line or a re-ordered inner statement no longer leaves a stale guard in the file.

### Added

- **`trinity --help` shows common-workflow examples** in its epilog (`setup`, `apply`, `apply --dry-run`, `apply --adopt-drift`, `restore`, `doctor`, `pause`/`resume`, etc.).
- **`TimeoutStopSec=30` is explicit in the systemd service template** (was the implicit 90s default; making it explicit documents the intent and surfaces a misbehaving D-Bus call sooner in the unit logs).
- **`CONTRIBUTING.md` documents the dev setup, quality gates, exit-code convention, provider/backend/descriptor authoring guides, and the security model.**
- **`.pre-commit-config.yaml`** wires up `ruff` (check + format) and `mypy` so local commits catch style and type issues before pushing.

### Internal

- **Test coverage rose from 79% to 84.5%.** `cli.py` 60% → 80%, `orchestrator.py` 69% → 80%, `atomic.py` 73% → 94%. Test count grew from 275 to 345 across new files `tests/test_exit_codes.py`, `tests/test_cli_coverage.py`, `tests/test_orchestrator_coverage.py`, `tests/test_main.py`, plus targeted additions to `tests/test_atomic.py`, `tests/test_cli.py`, `tests/test_qml_patch.py`, and `tests/test_systemd.py`.
- `validate_provider_options` no longer catches a bare `Exception`; it now narrows to `(pydantic.ValidationError, ValueError, TypeError)` so real bugs (NameError, etc.) surface as tracebacks instead of being rewritten as "rejected options".
- `_safe_probe` logs the exception class name (`error_type` field) in addition to the message, so a misbehaving third-party probe is identifiable from the journal without having to attach a debugger.

## [0.2.4] — 2026-07-13

### Fixed

- **A theme-fork failure no longer aborts the whole `apply`.** The SDDM fork step ran before the surface backends with no error handling, so on machines where `/usr/share/sddm/themes` requires root (i.e. most machines), the hourly user-mode timer run would crash with `PermissionError` before the desktop and lock screens were updated. The fork is now best-effort like the backends: the failure is reported with a sudo hint and the apply continues.

### Changed

- **The SDDM theme fork is now idempotent.** Previously every full `apply` deleted and re-copied the entire fork (churning the manifest and wiping drift backups inside the fork). The fork now records a content digest of its vendor source (`.trinity-fork-source`) and is only rebuilt when the vendor Breeze theme actually changes; the `trinity.conf` drop-in is only rewritten when missing (self-healing).
- **The fork and drop-in are skipped when plasmalogin is the active greeter** — SDDM is not in use there, so they were inert writes requiring root for nothing.
- CI workflows: bumped `actions/checkout` to v7, `actions/upload-artifact` to v7, and `astral-sh/setup-uv` to v7 (silences the Node 20 deprecation warnings).

### Documentation

- README (surfaces table, QML patching section, LLM-briefing invariant 10), `docs/config-reference.md`, and the `verify` skill now describe the theme-fork mechanism accurately: `Login.qml` is patched in the `trinity-breeze` fork, never in the vendor theme.

## [0.2.3] — 2026-07-13

### Added

- **SDDM Theme Forking Integration.** Instead of patching the vendor Breeze theme's `Login.qml` in place (which is vulnerable to being overwritten by Plasma upgrades), Trinity now copies the Breeze theme to `/usr/share/sddm/themes/trinity-breeze` and applies all token modifications to this custom fork. The active theme is selected via a drop-in file at `/etc/sddm.conf.d/trinity.conf`.
- **Improved Privilege UX.** When theme tokens are enabled, the privilege checks now accurately check write permissions for the custom drop-in config and the fork directories.

## [0.2.2] — 2026-07-12

### Added

- **Support for the `plasmalogin` (Plasma Login Manager) display manager.** Trinity now automatically detects when `plasmalogin` is the active display manager (replacing SDDM) and writes its wallpaper configuration drop-in file to `/etc/plasmalogin.conf.d/trinity.conf` instead of SDDM's `/usr/share/sddm/themes/breeze/theme.conf.user`.

## [0.2.1] — 2026-07-12

### Fixed

- **Drift backups are now capped at 3 per vendor file.** The existing
  content-dedupe only prevented duplicates of identical drifted
  content; during active iteration the vendor file changes between
  applies, which littered the system lockscreen directory with 100+
  timestamped `.trinity.drift.*` backups. `handle_drift` now prunes to
  the newest `_MAX_DRIFT_BACKUPS` (3) after creating a backup — the
  backup the current `DriftError` points at is always the newest, so
  it is never pruned. (Backups from older naming schemes, e.g.
  `.usurface.drift.*`, are not managed — remove those manually.)
- `tests/test_graceful_failure.py` built configs without
  `theme_tokens`, so the omitted-key auto-migration enabled QML
  patching and the pipeline probed real `/usr/share` vendor files from
  inside the test. Hermetic now (same fix as the CLI tests in 0.2.0).
- Docs claimed the HTTP layer does "IP-pinned DNS resolution"; it
  deliberately does not (pre-flight resolve + private-address
  rejection only, so TLS SNI keeps working). Wording corrected in the
  config reference and docstrings, and the unused `_pin_host` helper
  (plus its tests) removed.
- The `--restart-dm` privilege hint printed a raw uid comparison
  ("requested but 1000 != 0"); it now explains that root or
  sudo/pkexec is required.

## [0.2.0] — 2026-07-12

### Fixed

- **Desktop wallpaper now repaints when the image changes.** Wallpaper
  files are content-addressed (`last_wallpaper-<digest>.jpg`) instead
  of a fixed filename. Plasma's `org.kde.image` plugin doesn't watch
  file contents and KConfig only emits a change signal when the
  `Image=` *value* changes, so overwriting `last_wallpaper.jpg` in
  place updated the bytes on disk but never refreshed the running
  shell — the new picture only appeared after a plasmashell restart.
  Each apply keeps the previous generation and prunes older ones, and
  maintains a stable `last_wallpaper.jpg` symlink pointing at the
  current generation: SDDM re-reads the image at every greeter start,
  so its theme.conf.user references the fixed alias and stays current
  without needing a rewrite (it is usually root-owned, which the
  user-mode timer cannot touch).
- **The systemd user service failed to start on KDE Neon / Ubuntu
  24.04** with "Failed to drop capabilities: Operation not permitted".
  `ProtectClock=` and `ProtectKernelModules=` are implemented by
  dropping capabilities, which a *user* manager cannot do; both are
  removed from the unit template (they add nothing for an unprivileged
  service — it never holds CAP_SYS_TIME / CAP_SYS_MODULE).
- Two CLI tests ran `apply` with the default
  `/usr/local/share/wallpapers` shared dir and an unstubbed login
  backend, writing real system files on machines where those paths are
  user-writable. Both are hermetic now.

### Added

- **`rss` provider** — turns any RSS 2.0 / Atom feed that carries
  images (RSS `enclosure`, Media RSS `media:content` /
  `media:thumbnail`, Atom enclosure links, or a direct image `<link>`)
  into a wallpaper source. Shares the SSRF-hardened HTTP layer
  (HTTPS-only, private-address rejection, redirect/size caps) and
  parses feeds with `defusedxml` (rejects XXE, billion-laughs entity
  expansion, and DTD retrieval). New dependency: `defusedxml`.
- **`trinity apply --if-changed`** — asks the provider for a cheap
  change token (new `trinity_provider_probe` pluggy hook: Bing uses
  the metadata document's image hash, `rss`/`json-api` the resolved
  image URL, `file` a stat token, `solid` an options digest) and skips
  the download and all surface writes when nothing changed. State is
  persisted in `<user_dir>/refresh_state.json`; a missing/corrupt
  state or a probe failure degrades to a full apply (fail open).
  Providers without a probe fall back to a full fetch plus an
  image-digest comparison.
- **`trinity apply --restart-dm`** — opt-in flag that restarts the
  detected display manager after the login wallpaper was applied, so
  the new SDDM background is visible immediately. Terminates the
  running Wayland session, so it is never automatic: it requires the
  explicit flag plus root (or a sudo/pkexec escalation path), and
  falls back to the usual restart hint otherwise.

### Changed

- **The refresh timer polls hourly instead of daily at noon.** POTD
  sources publish at provider-specific times (Bing rotates the en-US
  image in the early morning UTC), so the fixed noon run lagged
  upstream by hours — and a reboot before noon showed yesterday's
  image on SDDM. The service now runs `apply --if-changed`, making
  the hourly poll a metadata-sized request when nothing changed.
  Re-run `trinity install` to regenerate the units.

### Changed (Phase 5 — sanctioned SDDM override mechanisms)

- **SDDM wallpaper backend no longer edits the vendor `theme.conf`**.
  Instead it writes `theme.conf.user` alongside the base config. SDDM
  merges the two (sanctioned mechanism, confirmed in
  `ThemeConfig.cpp:35-62`), so the vendor file is untouched and a
  Plasma upgrade doesn't blow away the edit. `restore` deletes
  `theme.conf.user`. This is the Tier-1 (wallpaper-only) path.
- **New `src/trinity/backends/sddm_fork.py`** — the Tier-2 (theme
  tokens) path forks the Breeze theme to
  `/usr/share/sddm/themes/trinity-breeze/`, records every file in the
  manifest, patches `metadata.desktop` to rename it to "Trinity
  Breeze" (so it's distinguishable in the SDDM theme picker), writes
  the drop-in `/etc/sddm.conf.d/trinity.conf` with
  `[Theme] Current=trinity-breeze`, and is fully reversible via
  `remove_fork` / `remove_dropin`. The vendor Breeze theme is never
  modified.
- **Lockscreen QML token patching keeps the Phase 4
  descriptor+canary approach** — the research spike
  (`docs/design/override-mechanisms.md`) confirmed LNF packages
  cannot override the lockscreen in Plasma 6 (the lockscreen QML
  comes from the `Plasma/Shell` package, not an LNF). A full shell
  package fork for ~3 property edits would mean maintaining ~10 QML
  files; the canary CI already surfaces upstream drift. The
  in-place patch path stays for the lockscreen.
- New design doc: `docs/design/override-mechanisms.md` with the
  research findings, conclusions per surface, and a manual
  validation checklist for the maintainer.
- Tests: 19 new cases (10 login `theme.conf.user` backend, 9 SDDM
  fork helper). Total 237 pass, 78.90% coverage, ruff/mypy clean.

### Added (Phase 4 — data-driven patch descriptors + upstream canary CI)

- The QML anchor regexes (managed font properties, fadeout-timer
  interval, wake-keypress guard) and the managed-property lists are
  now sourced from packaged TOML data files in
  `src/trinity/theme/descriptors/`, validated at load time against a
  pydantic schema. A malformed packaged descriptor is a bug and
  fails loudly at module import. A new descriptor file for a future
  Plasma layout can be added without a code change.
- Plasma version detection: `plasmashell --version` is parsed
  defensively (cached per run) and used to select the matching
  descriptor. Missing binary → unknown → skip token patching with a
  structured "theme tokens unsupported on Plasma X.Y" status in
  `apply`/`doctor` output. `$TRINITY_PLASMA_VERSION` env override is
  honoured for testing.
- Post-patch `qmllint` validation: after writing a patched QML file,
  `qmllint` is run (if available, 5 s timeout). On lint failure the
  file is rolled back to its pristine state via the manifest
  machinery and the failure is surfaced as a plan line; other
  surfaces (SDDM theme.conf, desktop/lock backends) still apply.
  Availability: `qml6-qttools` (Debian/Neon), `qt6-qtdeclarative-devel`
  (Fedora), `qt6-declarative` (Arch).
- Upstream Canary CI workflow (`.github/workflows/upstream-canary.yml`):
  weekly + manual dispatch, fetches the current Breeze SDDM and
  Plasma lockscreen QML from KDE invent, then runs
  `tests/canary/test_descriptor_anchors.py` which asserts every
  descriptor anchor still matches upstream. A failure is a red badge
  in README, not a release blocker — it surfaces an upcoming QML
  breakage so a new descriptor file can be added before the release
  lands.
- Tests: 27 new cases (18 descriptor loader + selection + version
  detection, 9 qmllint helper). Total 218 pass, 78.48% coverage.
- `logging_setup.configure_logging` now uses
  `cache_logger_on_first_use=False` so module-level `_log` aliases
  created before `configure_logging` is called (e.g. during test
  collection) re-resolve to structlog on first use rather than caching
  the unconfigured stdlib fallback — this was the root cause of a
  recurring `TypeError: Logger._log() got an unexpected keyword
  argument 'hint'` in tests.

### Added (Phase 3 — generic JSON-API provider + shared SSRF-hardened HTTP)

- New `json-api` built-in provider. Config-driven recipe that GETs a
  JSON metadata document, resolves an RFC 6901 JSON Pointer to an
  image URL, then downloads the image. Powers arbitrary metadata-then-
  image flows (NASA APOD, Wikimedia POTD, custom internal APIs) without
  writing a new Python plugin. Recipe fields: `metadata_url` (HTTPS,
  `AnyHttpUrl` validated at config load), `image_url_pointer`
  (RFC 6901), `params`, `headers`, `timeout` (0 < t ≤ 300).
- New `src/trinity/providers/builtin/_http.py` — shared SSRF-hardened
  HTTP machinery used by both `bing` and `json-api`:
    - HTTPS-only for both metadata and image URLs.
    - DNS pre-resolution with IP pinning (defends against DNS rebinding
      and reduces the trust surface to the resolved address).
    - IPv4 and IPv6 safe-address check (rejects private, loopback,
      link-local, reserved, multicast, IPv4-mapped-IPv6).
    - Per-hop redirect loop (5 hop cap) with re-validation of scheme,
      HTTPS, and SSRF safety on every hop.
    - Size caps: 5 MiB metadata, 50 MiB image (configurable).
    - Pre-flight `Content-Length` cap on the image response so a hostile
      server can't trick us into reading 50 MiB before the cap check.
    - Header and query-param count/length caps to bound config-driven
      attack surface.
- The `bing` provider has been refactored to use the shared
  `_http.fetch_metadata_json()` and `_http.download_image()` helpers
  (no behaviour change for callers; tests still cover the same
  scenarios plus the new shared SSRF paths).
- `resolve_pointer()` — full RFC 6901 implementation, including `~0`
  and `~1` escape decoding, numeric array indices, and clear errors
  for out-of-range / missing tokens.
- New tests: 22 cases covering happy path, relative-URL resolution,
  pointer escape sequences, non-string pointer targets, HTTPS-only
  enforcement, private-IP rejection, redirect-cap rejection, oversize
  metadata + image rejection, IPv4/IPv6 `Content-Length` parsing, port
  preservation in pinned URLs, and a hypothesis property test for
  `resolve_pointer`.

### Added (Phase 2 — theme_tokens opt-in + setup)

- New `[surface.theme_tokens] enabled` opt-in switch. When `false` (the
  default for new configs), `apply` skips all QML patching and drift
  checks, and `install` skips template extraction. The `fonts`, `login`,
  and `lock` config sections become inert when disabled. Pre-existing
  configs (v1, no key) are auto-migrated to `enabled = true` with a
  one-time deprecation log so existing users don't silently lose
  patching.
- Non-default token values with `theme_tokens.enabled = false` produce
  a structured warning (not a silent ignore).
- `trinity status` and `trinity doctor` report the theme-tokens state
  and skip drift checks when disabled.
- `config init` now writes `enabled = false` in the starter config.
- New `trinity setup` command: chains `config init` → `install` →
  `apply --dry-run` → confirm → `apply`. The recommended path for
  first-time users; `--yes` skips all prompts.
- README quickstart now uses `trinity setup` as the canonical entry
  point. `docs/config-reference.md` documents the new key.

### Added (Phase 1 — provider-declared option schemas)

- New `trinity_provider_options_schema` pluggy hookspec. Each built-in
  provider now declares a pydantic `BaseModel` (`extra="forbid"`) that
  validates its options at config load time. This catches option typos
  (e.g. `resoultion` instead of `resolution`) at `config validate` time
  rather than at fetch time (3am timer).
- `BingOptions`, `FileOptions`, `SolidOptions` — per-provider option
  models with full field constraints (regex patterns, ge/le bounds,
  enums). The ad-hoc numeric coercion in `fetch()` has been removed;
  the schema is the single source of truth.
- `trinity provider info <name>` now auto-renders an option table from
  the schema (field name, type, default, description).
- `load_config()` calls `validate_provider_options()` after pydantic
  schema validation, with a clear error message naming the config file
  and the offending field.
- Third-party providers that don't implement the schema hook fall back
  to the previous permissive behaviour with a logged warning (backward
  compatible).
- `src/trinity/providers/README.md` updated with the new hook contract.

### Changed (2026-07-10 relicensing)

- **License changed from PolyForm Noncommercial 1.0.0 to GPL-3.0-or-later.**
  This is a sole-author relicense with no external contributors to clear.
  The bundled Inter font remains under the SIL Open Font License 1.1.

### Changed (2026-07-10 deferred quality items)

- Renamed `src/trinity/logging.py` to `src/trinity/logging_setup.py` to
  avoid shadowing the stdlib `logging` module. All internal imports
  updated. No compatibility shim — this is a 0.1.0 application, not a
  library API.
- Added `hypothesis` as a test dependency (PEP 735 group) and introduced
  `tests/test_properties.py` with property-based tests for pure functions
  (`_to_toml`/`_toml_literal` round-trip, `_replace_property_values`
  idempotence, `strip_sentinels`/`normalize_managed_values` idempotence,
  `_parse_color` `#RGB`↔`#RRGGBB` equivalence, `ManifestEntry`
  JSON round-trip).
- `trinity apply` now installs a SIGTERM handler in the `run()` entry
  point so `finally` blocks and context managers unwind cleanly when
  systemd sends SIGTERM, instead of the process dying mid-write. Logs a
  structured `sigterm_received` event before shutting down.

### Changed (2026-07-10 quality sweep)

- Packaging: PEP 639 SPDX license expression (now `GPL-3.0-or-later`)
  with `license-files`; version is now single-sourced from
  `trinity.__version__` via hatch; `py.typed` marker shipped; sdist now
  includes `LICENSE`, `CHANGELOG.md`, `CONTRIBUTING.md`, and `docs/`;
  test dependencies moved from a published `test` extra to a PEP 735
  dependency group (`uv sync --group test`); unused `syrupy` test
  dependency removed.
- Bing provider wraps network failures (timeouts, DNS errors, non-2xx
  responses) in `ProviderError`, and both `bing` and `solid` validate
  numeric options instead of crashing on bad types. Fixed a latent
  `NameError` when Bing returns non-JSON metadata.
- `file` provider: the allow-list check now runs before the existence
  check (no longer discloses whether paths outside the allowed roots
  exist), and the allowed roots honour `HOME` changes at call time.
- `trinity uninstall` now removes the unit files from the correct
  directory (`~/.config/systemd/user/`; previously it looked in
  `~/.config/trinity/systemd/user/` and never deleted them) and runs
  `systemctl --user daemon-reload` afterwards.
- Atomic writes fsync the destination directory after rename for full
  crash consistency; the manifest append loops on short writes and
  opens the log with `O_CLOEXEC`.
- `systemctl` shell-outs return a clean failure instead of raising when
  `systemctl`/`sudo` is missing (non-systemd hosts).
- Systemd service template gained further hardening directives
  (`SystemCallArchitectures=native`, `RestrictRealtime`,
  `RestrictNamespaces`, `RestrictSUIDSGID`, `LockPersonality`,
  `ProtectKernelTunables/Modules`, `ProtectControlGroups`,
  `ProtectClock`, `ProtectHostname`, `UMask=0022`).
- `trinity apply` reports an invalid config as a clean `error:` block
  with a hint instead of an unexpected-error traceback.
- Removed dead code: `manifest.truncate`, `paths.last_wallpaper`,
  `paths.last_config_copy`, and the pre-3.10 `entry_points` fallback.
- CI: Python 3.12/3.13 matrix, locked installs (`uv sync --locked`),
  coverage floor enforcement, wheel/sdist build artefacts, Dependabot
  for GitHub Actions and uv dependencies.

### Added

- Initial implementation of `trinity` CLI.
- Atomic file writes (`trinity.atomic`).
- TOML configuration schema (`trinity.schema`).
- Bing, file, and solid-colour providers via `pluggy`-style registry.
- Third-party provider plugins are now loaded via the
  `trinity.providers` setuptools entry-point group (previously only
  built-ins were registered, despite the docs advertising extension).
- Append-only manifest with restore (`~/.local/state/trinity/manifest.jsonl`).
- Desktop, lock, and login backends.
- QML patching with sentinel markers + drift detection.
- Systemd user timer for daily refresh.
- `doctor` health check.
- `migrate-from-shell` helper.
- `qml-update-templates` maintenance command.
- Manifest compaction: after a successful `apply`, the log is bounded to
  the most recent 200 entries and orphaned snapshots are pruned, so undo
  history cannot grow unbounded under the daily systemd timer.

### Changed

- `trinity restore` now truncates the manifest after a successful
  restore: a full restore empties the log and prunes all snapshots; a
  partial restore (`--to <ts>`) keeps entries with `ts <= to` and prunes
  only the snapshots of reverted entries. A second restore is now a
  no-op rather than a replay.
- QML drift is no longer silently adopted as the new pristine baseline.
  `handle_drift` now raises `DriftError`; the user must explicitly
  consent via `trinity qml-update-templates` or `trinity apply
  --adopt-drift`.
- **Desktop wallpaper now actually updates and applies live.** The
  desktop backend previously wrote the flat `[Containments]` group
  (which Plasma ignores for wallpaper) and called a non-existent D-Bus
  method (`org.kde.plasma.desktop /PlasmaShell refreshWallpaper`). It now
  writes the real `[Containments][<id>][Wallpaper][org.kde.image]
  [General] Image=` group for every desktop containment and applies the
  change live via `org.kde.plasmashell /PlasmaShell evaluateScript` —
  the same path Plasma's settings UI uses — so the wallpaper swaps
  atomically with no visible reload/flip on login.
- **Lock screen wallpaper now reloads live.** After writing
  `kscreenlockerrc` the backend calls
  `org.freedesktop.ScreenSaver /org/freedesktop/ScreenSaver
  org.kde.screensaver.configure()` so the running kscreenlocker picks
  up the new image without needing to lock+unlock.
- `clock_format` and `font weight` are now validated (Qt date-time
  tokens; Qt weight tokens / 100-900). `font_install.is_installed` now
  uses `fc-match --format` for an exact family match instead of a
  substring match. The `file` provider refuses files larger than 100 MiB
  to bound memory use. The unused `fonttools` dev dependency was removed.

### Added (Appendix B)

- `trinity apply --adopt-drift`: when passed, a drifted QML file is
  adopted as the new pristine baseline (after a timestamped backup) and
  patched. Without the flag, drifted files are skipped with a hint.
- `trinity status` and `trinity doctor` now report any QML drift with
  the exact remediation commands.

### Fixed (Appendix A.4)

- The desktop backend no longer writes the flat ``[Containments]``
  group, which Plasma ignores for wallpaper and whose write can trigger
  a config-file reload that resets the desktop to the default image.
  Only the real nested ``[Containments][<id>][Wallpaper][org.kde.image]
  [General] Image=`` groups are written, plus the live
  ``evaluateScript`` apply.
- `trinity apply` now survives a manifest or shared directory owned by
  another user (e.g. after a previous `sudo` run) without crashing.
- Dry-run plan output now shows the real `kwriteconfig6 --group`
  argument sequence for nested INI groups.
- Shared wallpaper ownership is restored to the invoking user after a
  `sudo trinity apply`, so the daily user-mode timer stays writable.
- Manifest log and snapshots directory ownership are likewise restored
  to the invoking user after a `sudo trinity apply`.
- D-Bus live-update calls (`evaluateScript`, screen-saver reload) are
  routed through the invoking user's session bus when run via `sudo`.
- `~` in `surface.behaviour.user_dir`/`shared_dir` expands to the
  invoking user's home under `sudo`, not `/root`.
- Image extension is derived from the re-encoded bytes (JPEG/PNG), not
  the provider's original suggestion.
- The fadeoutTimer interval matcher tolerates other properties between
  `id: fadeoutTimer` and `interval:`.
- **Display manager restart hint:** when `trinity apply` actually
  changes the SDDM/plasmalogin `theme.conf`, the plan output names the
  active display manager unit and prints the exact
  `sudo systemctl restart <dm>` command needed to make the new login
  wallpaper visible ("switch user" reuses the existing greeter, which
  never re-reads `theme.conf`). The display manager is **never**
  restarted automatically — that would terminate the user's running
  session.
- **Test isolation:** `apply_to_surfaces(backends=[])` now truly skips
  all backends (previously the falsy empty list fell through to
  `default_backends()`, causing integration tests to invoke the real
  `kwriteconfig6` / `qdbus6` against the live Plasma session and
  pollute the user's real `appletsrc` with tmp wallpaper paths — the
  real root cause of the desktop reverting to the KDE Neon default
  after running the test suite). The CLI integration test now mocks the
  D-Bus live-apply calls so it never talks to the running shell.

### Known limitations

- Template versioning per Plasma minor release is deferred to v2.
