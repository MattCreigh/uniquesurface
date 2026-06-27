<div align="center">
  <h1>✨ uniquesurface ✨</h1>
  <p><strong>Unified Plasma 6 surface set manager</strong></p>

  [![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
  [![KDE Plasma 6](https://img.shields.io/badge/KDE-Plasma%206-1d99f3.svg?logo=kde)](https://kde.org/plasma-desktop/)
  [![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial-yellow.svg)](LICENSE)
  [![Tests](https://img.shields.io/badge/tests-77%20passed-success)](https://pytest.org/)
</div>

> [!WARNING]
> **AI-Generated Codebase**
> This entire architecture and codebase—including the strict schema validation, append-only undo manifest, sentinel-based QML patching, and tests—was generated autonomously by generative AI (Google DeepMind's models) in under an hour. While it passes all tests and is functionally robust, please review the code before deploying it in mission-critical environments.

One CLI, one configuration file, **three surfaces**: your desktop wallpaper, lock screen wallpaper, and SDDM login screen wallpaper, beautifully synchronized.

## 🌟 Why `uniquesurface`?

Existing wallpaper tools like `variety` or `nitrogen` only handle the desktop, and GUI-only projects like `PlasmaWallpaperManager` often patch vendor files irreversibly, frequently bricking systems after an update.

`uniquesurface` is the **CLI-first, provider-extensible, reversible, systemd-automated** option for KDE Plasma 6 users who want their desktop, lock, and login screens to be cohesive and trust their visuals will stay intact.

### ✨ Key Features
- **Total Cohesion**: Set one wallpaper and automatically apply it to SDDM, the lock screen, and the desktop simultaneously.
- **Provider Registry**: Built-in support for Bing Picture of the Day, local files, and solid colors. Extensible via a `pluggy` plugin model.
- **Atomic Rollbacks**: Every file change is written to an append-only undo log. Made a mistake? `usurface restore` rolls back to a pristine state.
- **Safe QML Patching**: Uses a sentinel-based QML patcher with drift detection. If an upstream KDE update alters a file, `uniquesurface` will safely detect the drift instead of bricking your login screen.
- **Automated Refreshes**: Automatically installs systemd user timers for a daily wallpaper refresh.
- **Strict Configuration**: Pydantic-powered TOML schema ensures zero typos and deterministic execution.

## 🚀 Quickstart

### Installation

Install the package via `uv`:
```sh
uv tool install /home/matt/Projects/background_manager
```

### Setup & Migration

If you are migrating from a previous shell-based setup, generate a starter config first:
```sh
usurface migrate-from-shell
```

Install the bundled fonts, QML templates, and enable the systemd timer. *(Note: this step uses `sudo` internally for system-wide font and directory setup, but safely runs systemd user services under your own desktop user.)*
```sh
sudo usurface install
```

### Usage

Preview the changes the tool will make:
```sh
usurface apply --dry-run
```

Apply your configured wallpaper to all three surfaces:
```sh
usurface apply
```

Check the health of your installation (verifies drift, font cache, config, and file permissions):
```sh
usurface doctor
```

Revert changes if needed:
```sh
usurface restore
```

## 📖 Documentation

- [**PLAN.md**](PLAN.md) — The comprehensive design, implementation specification, and architecture overview.
- [**Config Reference**](docs/config-reference.md) — Documentation for every configuration key.
- [**Migration Guide**](docs/migration-from-shell.md) — Migrating from existing shell-based implementations.

## 🛠️ Under the Hood

`uniquesurface` strictly adheres to Linux and XDG standards:
- **No shell scripts**: 100% Python implementation with rigorous test coverage.
- **Atomic I/O**: Temporary-then-replace logic with `fsync` guarantees no corrupted Plasma state during power loss.
- **Idempotent**: Re-running commands will safely produce the same state without duplicate operations.

<div align="center">
  <i>Crafted with 🩵 for KDE Plasma.</i>
</div>
