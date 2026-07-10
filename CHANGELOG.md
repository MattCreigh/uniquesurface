# Changelog

All notable changes to `trinity` are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/) and this project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

- Packaging: PEP 639 SPDX license expression (`PolyForm-Noncommercial-1.0.0`)
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
