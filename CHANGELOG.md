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

### Known limitations

- No vendored Inter font bundled (font install requires the user to
  provide one or install Inter system-wide themselves). The
  `font_install` module ships ready to copy a font when present.
- Template versioning per Plasma minor release is deferred to v2.
