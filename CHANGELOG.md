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

### Known limitations

- No vendored Inter font bundled (font install requires the user to
  provide one or install Inter system-wide themselves). The
  `font_install` module ships ready to copy a font when present.
- Template versioning per Plasma minor release is deferred to v2.
