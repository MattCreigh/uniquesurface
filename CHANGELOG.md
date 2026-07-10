# Changelog

All notable changes to `trinity` are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/) and this project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
