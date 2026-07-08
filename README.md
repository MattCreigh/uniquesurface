<div align="center">
  <h1>✨ uniquesurface ✨</h1>
  <p><strong>Unified Plasma 6 surface-set manager — desktop, lock screen, and SDDM login, in sync.</strong></p>

  [![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
  [![KDE Plasma 6](https://img.shields.io/badge/KDE-Plasma%206-1d99f3.svg?logo=kde)](https://kde.org/plasma-desktop/)
  [![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial-yellow.svg)](LICENSE)
  [![Tests](https://img.shields.io/badge/tests-109%20passed-success)](https://pytest.org/)
</div>

> [!NOTE]
> **AI-Assisted Development**
> Portions of this codebase were developed with the assistance of generative AI. The code is fully tested, but please review it before deploying in mission-critical environments.

One CLI, one config file, **three surfaces**: desktop, lock screen, and SDDM login screen, kept synchronized.

---

## 🌟 Why `uniquesurface`?

Existing wallpaper tools like `variety` or `nitrogen` only handle the desktop. GUI projects like `PlasmaWallpaperManager` often patch vendor files irreversibly and brick systems after a KDE update.

`uniquesurface` is the **CLI-first, reversible, systemd-automated** option for KDE Plasma 6 users who want a cohesive look across all three surfaces — and trust that their visuals (and their login screen) will stay intact.

### ✨ Key features

- **Total cohesion** — one wallpaper applied to desktop and lock screen at once, plus SDDM login when run with root.
- **Provider registry** — built-in Bing Picture of the Day, local files, and solid colours; built on a [`pluggy`](https://pluggy.readthedocs.io/) hook model (third-party entry-point loading is implemented but not validated with an external package yet).
- **Atomic rollbacks** — every file change is written to an append-only undo log. `usurface restore` replays the inverse operations newest-first.
- **Safe QML patching** — sentinel-based patching with drift detection. If an upstream KDE update alters a file, `uniquesurface` detects the drift instead of bricking your login screen.
- **Automated refreshes** — installs a systemd user timer for a daily wallpaper refresh.
- **Strict configuration** — pydantic-validated TOML schema catches typos before they reach your system.

---

## 🚀 Quickstart

### Requirements

- **KDE Plasma 6** (tested on 6.7 / Neon 24.04)
- **Python 3.12+**
- `kwriteconfig6` and `qdbus6` (provided by `plasma-workspace`)
- [`uv`](https://docs.astral.sh/uv/) (recommended installer) or `pip`

### Install

```sh
# from a local clone
uv tool install .

# …or directly from GitHub
uv tool install git+https://github.com/MattCreigh/uniquesurface.git
```

This installs the `usurface` console script on your PATH.

### First-time setup

Generate a starter config:

```sh
usurface config init
```

Edit `~/.config/usurface/config.toml` to pick a provider (default: Bing POTD). See the [config reference](docs/config-reference.md) for every key.

Then install the bundled font, shared wallpaper directory, and systemd timer:

```sh
sudo usurface install
```

> The `sudo` is only for the system-wide font, `/usr/local/share/wallpapers`, and SDDM `theme.conf` steps; the systemd user timer is enabled under your own desktop user.

### Applying the wallpaper

```sh
usurface apply            # desktop + lock screen + login (login needs root)
sudo usurface apply       # apply all three surfaces, including SDDM login
usurface apply --dry-run  # preview without writing
```

### Other commands

```sh
usurface status           # show config + recent manifest entries
usurface doctor           # verify drift, fonts, config, permissions
usurface restore          # revert every recorded change
usurface pause            # temporarily stop the daily timer
usurface resume           # re-enable the daily timer
```

---

## 🧩 Providers

List available providers:

```sh
usurface provider list
#   bing    [built-in]   Bing Picture of the Day.
#   file    [built-in]   Local image file.
#   solid   [built-in]   Solid colour or gradient.
```

Get details on one:

```sh
usurface provider info bing
```

Built-ins are registered through [`pluggy`](https://pluggy.readthedocs.io/). Third-party entry-point loading is also implemented in `make_plugin_manager` via `importlib.metadata.entry_points(group="usurface.providers")`; see [`src/usurface/providers/README.md`](src/usurface/providers/README.md).

---

## ⚙️ Configuration

Config lives at `~/.config/usurface/config.toml`. A minimal example:

```toml
[surface]
schema_version = 1

[surface.source]
provider = "bing"

[surface.source.options]
mkt = "en-US"
resolution = "1920x1080"
index = 0          # 0 = today, 1 = yesterday, …

[surface.fonts]
family = "Inter"
weight = "Normal"
password_character = "●"

[surface.behaviour]
shared_dir = "/usr/local/share/wallpapers"
user_dir = "~/.local/state/usurface"
```

Full reference: [`docs/config-reference.md`](docs/config-reference.md).

---

## 🔧 How it works

`apply` runs this pipeline:

1. **Fetch** the image from the configured provider.
2. **Verify** it with Pillow (decode + re-encode, strip metadata).
3. **Write** the image to `~/.local/state/usurface/last_wallpaper.jpg` and the SDDM-readable `/usr/local/share/wallpapers/last_wallpaper.jpg`.
4. **Apply** to each surface:
   - **Desktop** — `kwriteconfig6` on the nested `[Containments][<id>][Wallpaper][org.kde.image][General] Image=` groups in `plasma-org.kde.plasma.desktop-appletsrc` + `qdbus6 org.kde.plasmashell /PlasmaShell evaluateScript`.
   - **Lock** — `kwriteconfig6` on `kscreenlockerrc` (`[Greeter][Wallpaper][org.kde.image][General] Image=`).
   - **Login (SDDM/plasmalogin)** — rewrites `background=` in the Breeze theme's `theme.conf` (needs root). To see the change on the greeter, restart the display manager (e.g. `sudo systemctl restart plasmalogin`) — "switch user" does not reload `theme.conf`.
5. **Patch QML** for font/theme tokens where the vendor file declares them (skipped otherwise — never appends a block that would break the greeter).
6. **Record** every change in the manifest for `restore`.

Design principles:

- **No shell scripts** — 100% Python with a full test suite.
- **Atomic I/O** — temp-then-replace with `fsync`.
- **Idempotent** — re-running produces the same state, no duplicates.
- **Reversible** — every write is tracked; `restore` replays inverse operations newest-first.

---

## 📖 Documentation

- [**PLAN.md**](PLAN.md) — design, architecture, and implementation spec.
- [**Config reference**](docs/config-reference.md) — every configuration key.
- [**Migration guide**](docs/migration-from-shell.md) — coming from a shell-based setup.

---

## 🧪 Development

```sh
git clone https://github.com/MattCreigh/uniquesurface.git
cd uniquesurface
uv sync            # create venv + install dev deps
uv run pytest -q   # run the test suite (109 tests)
uv run ruff check src tests
uv run mypy src tests
```

---

## 📜 License

[PolyForm Noncommercial 1.0.0](LICENSE) — free for personal, non-commercial use.

---

<div align="center">
  <i>Crafted with 🩵 for KDE Plasma.</i>
</div>