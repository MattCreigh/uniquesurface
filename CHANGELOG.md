# Changelog

All notable changes to `uniquesurface` are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/) and this project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Initial implementation of `usurface` CLI.
- Atomic file writes (`usurface.atomic`).
- TOML configuration schema (`usurface.schema`).
- Bing, file, and solid-colour providers via `pluggy`-style registry.
- Third-party provider plugins are now loaded via the
  `usurface.providers` setuptools entry-point group (previously only
  built-ins were registered, despite the docs advertising extension).
- Append-only manifest with restore (`~/.local/state/usurface/manifest.jsonl`).
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

- `usurface restore` now truncates the manifest after a successful
  restore: a full restore empties the log and prunes all snapshots; a
  partial restore (`--to <ts>`) keeps entries with `ts <= to` and prunes
  only the snapshots of reverted entries. A second restore is now a
  no-op rather than a replay.
- QML drift is no longer silently adopted as the new pristine baseline.
  `handle_drift` now raises `DriftError`; the user must explicitly
  consent via `usurface qml-update-templates` or `usurface apply
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

- `usurface apply --adopt-drift`: when passed, a drifted QML file is
  adopted as the new pristine baseline (after a timestamped backup) and
  patched. Without the flag, drifted files are skipped with a hint.
- `usurface status` and `usurface doctor` now report any QML drift with
  the exact remediation commands.

### Fixed (Appendix A.4)

- `usurface apply` now survives a manifest or shared directory owned by
  another user (e.g. after a previous `sudo` run) without crashing.
- Dry-run plan output now shows the real `kwriteconfig6 --group`
  argument sequence for nested INI groups.
- Shared wallpaper ownership is restored to the invoking user after a
  `sudo usurface apply`, so the daily user-mode timer stays writable.
- D-Bus live-update calls (`evaluateScript`, screen-saver reload) are
  routed through the invoking user's session bus when run via `sudo`.
- `~` in `surface.behaviour.user_dir`/`shared_dir` expands to the
  invoking user's home under `sudo`, not `/root`.
- Image extension is derived from the re-encoded bytes (JPEG/PNG), not
  the provider's original suggestion.
- The fadeoutTimer interval matcher tolerates other properties between
  `id: fadeoutTimer` and `interval:`.

### Known limitations

- Template versioning per Plasma minor release is deferred to v2.
