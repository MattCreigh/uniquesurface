# PLAN: `trinity` — Unified Plasma 6 Surface Set Manager

> **Audience.** A future Claude/agent instance. Treat this document as the
> authoritative brief. Do not start implementation without reading it end to
> end. Do not re-litigate decisions marked **DECIDED** — only fill in the
> `OPEN QUESTIONS` section at the bottom or flag a blocker to the user.

---

## 1. Goals (in scope)

1. **One tool, three surfaces.** A single CLI (`trinity`) and single
   configuration file that simultaneously drive:
   - Desktop wallpaper (Plasma shell `Containment` wallpaper plugin)
   - Lock screen wallpaper (kscreenlocker / `~/.config/kscreenlockerrc`)
   - Login screen wallpaper (SDDM/plasmalogin Breeze theme)

2. **Source abstractions.** A "surface set" (the trio above) can be sourced
   from:
   - A local image file
   - A solid colour / gradient
   - A built-in Pictures-of-the-Day provider (Bing first; Picsum,
     NASA APOD, Wikimedia later)
   - A user-defined provider plugin (entry point hook — see §7.5)

3. **Font and theme tokens.** Centralised tokens for the login + lock QML:
   `font_family`, `font_weight`, `password_character`, `clock_format`,
   `accent_color`. Editing any token re-renders both screens consistently.
   - **DECIDED** (per user, 2026-06-27): Login and lock screens will use a
     *fancier font* than the current Lato default. Plan picks **Inter**
     (open, OFL, complete Latin coverage) for `font_family` by default.
     **Only a static Regular .ttf is vendored and installed.** Variable
     fonts are intentionally avoided because the SDDM/plasmalogin QML
     `FontLoader` handles static fonts more reliably.
   - **DECIDED** (per user, 2026-06-27): The font is installed into
     `/usr/local/share/fonts/` at `trinity install` time, **requiring
     root** for that step only. A user-local install (`~/.local/share/fonts/`)
     is **not** used because the SDDM greeter runs as the `sddm` user and
     cannot read another user's home directory. `trinity install` will
     ask for `sudo` and fall back gracefully if declined (login screen
     keeps the system default font).

4. **Day-rollover automation.** A user-level systemd timer refreshes the
   Picture-of-the-Day source once per day. Wired up automatically by
   `trinity install`.

5. **Single point of revert.** `trinity restore` reverts **every** surface
   to a pre-managed state in one command.

6. **Differentiation.** Existing tools either cover only the desktop
   (`variety`, `nitrogen`) or are GUI-only weekend projects that patch
   vendor files without reversibility (`PlasmaWallpaperManager`).
   `trinity` is the CLI-first, provider-extensible, reversible,
   systemd-automated option for users who want to *trust* their login +
   lock + desktop visuals.

## 2. Non-goals (out of scope, do NOT implement)

- A GUI. CLI + declarative config is enough. (A Qt/C++ plasmoid would be a
  v2 effort; defer.)
- KWin effects, colours, cursor themes. Out of scope. Stay focused on
  *surfaces* (wallpaper + login + lock visuals).
- LightDM / GDM / other display managers. **plasmalogin / SDDM only.**
  The lock screen component (kscreenlocker) is universal to KDE so we do
  support it everywhere.
- Replacing the Breeze SDDM theme wholesale. We **patch** it, like we do
  today. The user has rejected rewriting the theme.

## 3. Hard constraints (must hold true at all times)

| # | Constraint |
|---|------------|
| C1 | **No shell scripts.** The implementation language is Python (with Rust optional for performance-critical paths). No `.sh` files in the repo or installed by the package. |
| C2 | **`uv`-managed Python project.** Tooling standard: `uv` for venv, deps, lockfile, and `uv tool install` / `uvx` for global CLI. No `pip`, no `pipx`, no `pyproject.toml` `setuptools`-only flows. |
| C3 | **No sudo required for normal use.** Wallpaper changes are user-config. Sudo is only required at `trinity install` time for: (a) copying the font to `/usr/local/share/fonts/`, (b) creating the plasmalogin-visible shared wallpaper directory (normally `/usr/local/share/wallpapers/`, or `/var/cache/trinity/wallpapers/` if `/usr/local/` is read-only), and (c) patching root-owned SDDM/Plasma QML files. All three are opt-in / explicit. Normal `trinity apply` never needs root. |
| C4 | **plasmalogin must read the chosen wallpaper.** This means the wallpaper file at the SDDM path must be world-readable, owned by a user the plasmalogin service can traverse to. Existing setup uses `/usr/local/share/wallpapers/` for this. Plan keeps that path. |
| C5 | **Atomic writes.** Every config file rewrite uses tmp-then-`replace` with fsync. Power-cut mid-write must not leave a corrupted plasma state. |
| C6 | **Idempotent.** Running `trinity apply` twice with the same config produces the same on-disk state. Re-running `trinity install` is safe. |
| C7 | **Plasma 6 + Wayland only.** No Qt5 / X11 paths. Use `kreadconfig6`, `kwriteconfig6`, `qdbus6`. |
| C8 | **Reversible.** Every file the package writes is registered in an append-only manifest at `~/.local/state/trinity/manifest.jsonl`. `trinity restore` reads it newest-first and replays inverse ops. |

## 4. Current state inventory (as of 2026-06-27)

| Asset | Location | Owner | Notes |
|---|---|---|---|
| POTD shell script | `/usr/local/bin/bing-potd.sh` | root | Bash; uses `curl` + python3 JSON parse. Will be **deleted** by the new package. |
| POTD shell script backup | `/usr/local/bin/bing-potd.sh.bak` | root | Keep until migration is verified. |
| POTD systemd service | `~/.config/systemd/user/bing-potd.service` | matt | Will be replaced. |
| POTD systemd timer | `~/.config/systemd/user/bing-potd.timer` | matt | Will be replaced. |
| Wallpaper (per-user) | `~/Pictures/Wallpapers/bing-potd.jpg` | matt | 1920×1080 JPEG, 333 KB. |
| Wallpaper (shared, plasmalogin-readable) | `/usr/local/share/bing-wallpapers/bing-potd.jpg` | matt: 644 | The crucial file. SDDM theme points here. |
| SDDM theme config | `/usr/share/sddm/themes/breeze/theme.conf` | root | `background=/usr/local/share/bing-wallpapers/bing-potd.jpg`. Patched. |
| SDDM `Login.qml` (patched) | `/usr/share/sddm/themes/breeze/Login.qml` | root | `passwordCharacter: "*"`, Lato font set. Backed up. |
| Lock screen config | `~/.config/kscreenlockerrc` | matt | Plugin `org.kde.image`, `Image=file:///usr/local/share/...`. |
| Desktop wallpaper config | `~/.config/plasma-org.kde.plasma.desktop-appletsrc` | matt | `wallpaperplugin=org.kde.image`; `Image=` set. |
| Plasma lock screen QML (patched) | `/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/MainBlock.qml` | root | Lato + `passwordCharacter: "*"` set. |
| Plasma lock screen QML (patched) | `…/lockscreen/LockScreenUi.qml` | root | consumeNextKey flag added (suppresses wake-up key). |
| Backups | `*.bak.20260627_085322` next to each patched root file | root | Keep; the new package should not delete them automatically. |

## 5. Target architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                       trinity (CLI entry)                         │
└────────────────────────────────────────────────────────────────────┘
                                  │
   ┌───────────────────┬──────────┴─────────────┬───────────────────┐
   ▼                   ▼                        ▼                   ▼
┌────────┐       ┌─────────────┐         ┌──────────────┐     ┌──────────┐
│ config │       │  provider   │         │  backends    │     │ manifest │
│  load  │       │  registry   │         │ (writers)    │     │  store   │
└────────┘       └─────────────┘         └──────────────┘     └──────────┘
       │                │                       │
       │                │              ┌────────┼────────────┐
       │                │              ▼        ▼            ▼
       │                │       ┌────────┐ ┌────────┐  ┌──────────┐
       │                │       │ desktop│ │  lock  │  │  login   │
       │                │       │   wp   │ │ screen │  │  screen  │
       │                │       └────────┘ └────────┘  └──────────┘
       │                │
       ▼                ▼
┌─────────────┐   ┌──────────────────────────────────────────────────┐
│ schema      │   │  providers/                                      
│  (pydantic) │   │  ├── bing.py          (default, ships in repo)   │
└─────────────┘   │  ├── solid.py         (single colour)            │
                  │  ├── file.py          (local image)              │
                  │  └── [user plugins]   (entry-points)             │
                  └──────────────────────────────────────────────────┘
                                  │
                                  ▼
                  ┌──────────────────────────────────────────────────┐
                  │  state store                                     │
                  │  ~/.local/state/trinity/                        │
                  │  ├── manifest.jsonl   (append-only undo log)     │
                  │  ├── last_wallpaper.jpg                          │
                  │  └── last_config.toml                            │
                  └──────────────────────────────────────────────────┘
```

**Data flow on `trinity apply`:**

1. Load + validate config from `~/.config/trinity/config.toml`
2. Resolve source → fetch / pick image → write to
   `~/.local/state/trinity/last_wallpaper.jpg` (atomic)
3. Verify the resolved image with Pillow (decode, ensure non-empty,
   optionally strip EXIF), then mirror to
   `/usr/local/share/wallpapers/last_wallpaper.jpg` with mode `0644`
   (if writable; else warn and require `sudo trinity share`)
4. Write desktop `appletsrc` entry — via `kwriteconfig6`
5. Write `~/.config/kscreenlockerrc` — via `kwriteconfig6`
6. Patch `/usr/share/sddm/themes/breeze/theme.conf` — via plain text edit,
   preserving comments; only touch `background=` line. (If running as
   non-root and root-writable, error and ask for `sudo`.)
7. If `font_family` / `password_character` / etc. tokens differ from
   shipped defaults, re-patch SDDM `Login.qml` and Plasma
   `MainBlock.qml`/`LockScreenUi.qml`. First run uses a diff against the
   pristine template and inserts sentinel markers; subsequent runs replace
   only the marked region. Same root/sudo gate.
8. Update manifest. Print summary. Exit 0.

## 6. Language + tooling choices

### 6.1 Python (primary)

- **Version:** 3.12+ (matches current system Python).
- **Manager:** `uv`. Project layout: `src/trinity/`, `pyproject.toml`
  declares the package, `uv` handles the venv.
- **Key deps:**
  - `pydantic` — config schema, validation
  - `httpx` — modern HTTP client for providers (replaces `curl`)
  - `Pillow` — image decode / resize / re-encode
  - `platformdirs` — XDG-aware paths (`XDG_STATE_HOME`, etc.)
  - `tomli` (≤ py3.11) / tomllib stdlib (3.12+) — config parsing
  - `structlog` — JSON logs, plumbed through systemd journal
  - `pluggy` (pytest's plugin lib) — provider entry points
  - `click` — CLI (or `typer` if user prefers; **DECIDED**: `click` for
    transparency)
- **No `requests`.** `httpx` is the modern choice and has sync + async.

### 6.2 Rust (optional, only if a benchmark demands it)

- Reserved for image processing if Pillow proves too slow on this laptop.
  The current daily job fetches + re-encodes a single 1920×1080 image —
  this is well within Pillow's capability. **DECIDED**: do not introduce
  Rust in v1. The pyproject reserves the `trinity-core` optional extra
  in case a future Rust extension is needed.
- If Rust is added later: a `rust/` workspace producing a
  `trinity_core` Python extension via `pyo3` + `maturin`. Keep the
  interface identical.

### 6.3 Build / release

- `uv build` produces wheel + sdist.
- `uv tool install trinity` installs the CLI to `~/.local/bin/trinity`.
- Tagging `v0.1.0` etc. via git tags. CHANGELOG.md maintained by hand
  until first release (Keep-a-Changelog format).

## 7. Package layout

```
background_manager/
├── PLAN.md                          (this file)
├── README.md                        (quickstart for humans)
├── pyproject.toml                   (uv project)
├── uv.lock                          (committed after first install)
├── .python-version                  (3.12)
├── src/
│   └── trinity/
│       ├── __init__.py
│       ├── __main__.py              (python -m trinity)
│       ├── cli.py                   (click app: install, apply, restore, status)
│       ├── config.py                (pydantic models, loader)
│       ├── schema.py                (SurfaceSet, Source, FontTokens, etc.)
│       ├── paths.py                 (XDG paths, shared wallpapers dir)
│       ├── manifest.py              (state store, write/read)
│       ├── atomic.py                (atomic_replace, fsync)
│       ├── backends/
│       │   ├── __init__.py
│       │   ├── base.py              (Backend protocol)
│       │   ├── desktop.py           (plasma-org.kde.plasma.desktop-appletsrc)
│       │   ├── lock.py              (kscreenlockerrc)
│       │   └── login.py             (SDDM theme.conf + Login.qml patch)
│       ├── providers/
│       │   ├── __init__.py          (pluggy hookspecs)
│       │   ├── registry.py
│       │   ├── builtin/
│       │   │   ├── bing.py
│       │   │   ├── file.py
│       │   │   └── solid.py
│       │   └── README.md            (how to add a 3rd-party provider)
│       ├── theme/
│       │   ├── __init__.py
│       │   ├── tokens.py            (default font, colour, character)
│       │   ├── font_install.py      (copies Inter .ttf to /usr/local/share/fonts/)
│       │   ├── fonts/
│       │   │   └── Inter-Regular.ttf   (vendored static .ttf, ~300KB)
│       │   ├── qml_patch.py         (parse+modify SDDM Login.qml, MainBlock.qml)
│       │   ├── drift.py             (template hash + marker-region detection)
│       │   └── extract.py           (copy pristine vendor QML into state dir)
│       ├── systemd/
│       │   ├── __init__.py
│       │   └── writer.py            (renders .service + .timer from inline templates)
│       └── logging.py
├── tests/
│   ├── conftest.py
│   ├── test_atomic.py
│   ├── test_config.py
│   ├── test_backends_desktop.py
│   ├── test_backends_lock.py
│   ├── test_backends_login.py
│   ├── test_providers_bing.py       (with `respx` for httpx mocking)
│   ├── test_qml_patch.py            (snapshot-based)
   ├── test_drift.py                (template-hash snapshots)
   └── fixtures/
│       ├── sample_config.toml
│       ├── sample_login.qml
│       └── sample_mainblock.qml
├── packaging/
│   ├── deb/                         (optional — deb build later)
│   └── arch/                        (optional — PKGBUILD later)
└── docs/
    ├── config-reference.md
    └── migration-from-shell.md
```

## 8. Configuration schema (the "surface set" data model)

`~/.config/trinity/config.toml`:

```toml
[surface]
# Where this user-defined surface set lives in the schema version space.
schema_version = 1

[surface.source]
# Provider plugin name (entry-point). One of the built-ins or a user plugin.
provider = "bing"

[surface.source.options]
# Provider-specific options; validated against the provider's pydantic model.
mkt = "en-US"
resolution = "1920x1080"

[surface.fonts]
# Font tokens applied to login + lock screens.
family = "Inter"
weight = "Normal"
password_character = "*"

[surface.login]
# Login-screen specific tokens.
clock_format = "hh:mm"
accent_color = "#1d99f3"
show_user_list = true

[surface.lock]
# Lock-screen specific tokens (rarely overridden).
on_idle_dim_seconds = 10
suppress_wake_keypress = true   # consumes the first keypress after wake

[surface.behaviour]
# Where to place the wallpaper file.
shared_dir = "/usr/local/share/wallpapers"   # world-readable, plasmalogin-visible
user_dir   = "~/.local/state/trinity"       # per-user canonical copy
```

Pydantic models in `src/trinity/schema.py` mirror this exactly. Any
unknown key raises a hard validation error at load time. This is
intentionally strict — typos are common failure modes for TOML config.

## 9. Implementation phases

Each phase ends with a green test run and a commit. **Do not advance to
the next phase with failing tests or uncommitted work.**

### Phase 0 — Repo skeleton (½ day)

- Create `pyproject.toml`, `uv.lock`, `.python-version`, `README.md`.
- Initialise `src/trinity/__init__.py` and a stub CLI that prints
  `trinity 0.0.0`.
- Run `uv sync`, `uv run trinity --help`. Commit.
- **Done means:** `uv tool install -e .` works and the binary is on PATH.

### Phase 1 — Atomic file I/O + paths (½ day)

- `atomic.py`: `atomic_write_bytes(path, data)` — tmp + fsync + rename.
- `paths.py`: wrap `platformdirs` to expose `state_dir()`, `config_dir()`,
  `cache_dir()`, `shared_wallpapers_dir()`.
- Tests: power-cut simulation (kill -9 mid-write? use monkey-patch + crash
  injection on tmp file).
- **Done means:** `tests/test_atomic.py` green.

### Phase 2 — Schema + config loader (1 day)

- `schema.py` with pydantic models.
- `config.py` with `load_config(path)` / `dump_config(model, path)`.
- Strict validation. Clear error messages.
- Tests: invalid TOML, missing keys, wrong types, unknown provider.
- **Done means:** `trinity config validate` exits 0 on a sample file, 1
  with a clear message on a broken file.

### Phase 3 — Provider registry (1 day)

- `providers/__init__.py`: pluggy hookspecs.
- `providers/builtin/bing.py`: fetch Bing metadata, parse, fetch image.
  This is the same logic as today's `bing-potd.sh` minus the shell.
- `providers/builtin/file.py`: validate + copy local file.
- `providers/builtin/solid.py`: PIL `Image.new` + `ImageDraw` solid colour
  or simple gradient.
- `providers/registry.py`: load all built-ins + 3rd-party via entry points.
- Tests: `respx` for httpx mocking. Snapshot image bytes.
- **Done means:** `trinity fetch --provider bing --out /tmp/x.jpg` works
  end-to-end.

### Phase 4 — Manifest store (½ day)

- `manifest.py`: append-only JSONL at
  `~/.local/state/trinity/manifest.jsonl`. Each line:
  ```json
  {"ts": "...", "op": "write", "path": "...", "prev_sha256": "...", "new_sha256": "..."}
  ```
- `restore()` reads newest-first, replays inverse ops until either the
  manifest is empty or the user passes `--to <ts>`.
- Tests: round-trip apply → restore → assert no diffs vs. pre-apply state.
- **Done means:** `tests/test_manifest.py` green.

### Phase 5 — Backends (2 days)

For each backend: implement writer + tests using a tmp HOME.

1. **`desktop.py`** — `~/.config/plasma-org.kde.plasma.desktop-appletsrc`.
   - Use `kwriteconfig6 --file … --group … --key …` (already on PATH).
   - Wrap the subprocess call in a `Backend` protocol.
   - After write, call `qdbus6 org.kde.plasma.desktop /PlasmaShell
     refreshWallpaper` so the change is live without re-login.
2. **`lock.py`** — `~/.config/kscreenlockerrc`.
   - Same pattern. Re-launching `kscreenlocker_greet --testing` is the
     visual check.
3. **`login.py`** — `/usr/share/sddm/themes/breeze/{theme.conf,Login.qml}`.
   - **theme.conf**: line-targeted regex edit. Preserve comments.
   - **Login.qml / MainBlock.qml / LockScreenUi.qml**: first-run uses
     diff against the pristine template; subsequent runs use sentinel
     markers (`/* @trinity:start */` … `/* @trinity:end */`). See
     §10.2. If the file has already been patched by hand, the migration
     helper must use the existing `.bak.*` files as the pristine
     baseline, not the current patched file.
- **Done means:** on a test VM, `trinity apply` flips all three surfaces
  to a known colour and they all show it.

### Phase 6 — Font installation + template extraction (½ day)

- `theme/font_install.py`: copy `Inter-Regular.ttf` (vendored) to
  `/usr/local/share/fonts/trinity/Inter-Regular.ttf`, then run
  `fc-cache -f`. Requires root; asks for `sudo`.
- `theme/qml_patch.py`: first-run diff patch; subsequent runs use
  sentinel markers to replace font/theme tokens in `Login.qml`,
  `MainBlock.qml`, `LockScreenUi.qml`.
- `theme/drift.py`: detect template drift by hashing the file with the
  marker region removed; save drift backups and re-extract from system.
- `theme/extract.py`: read pristine vendor QML files from
  `/usr/share/sddm/themes/breeze/` and `/usr/share/plasma/shells/...` and
  copy them into `~/.local/state/trinity/templates/`. Called by
  `trinity install` and by `trinity qml-update-templates`.
- **Done means:** login screen visibly renders Inter instead of Lato and
  state-directory templates exist.

### Phase 7 — Systemd units (½ day)

- `systemd/writer.py`: render `.service` + `.timer` from Jinja templates.
- `trinity install` enables the timer with `systemctl --user`.
- `trinity uninstall` disables + removes the unit files.
- **Done means:** `systemctl --user list-timers` shows `trinity-pull.timer`.

### Phase 8 — CLI wiring (1 day)

- `cli.py` with click. Subcommands:
  - `install` — install font to `/usr/local/share/fonts/` (root), create
  `shared_dir` (root), and set up systemd user units + manifest dir.
  - `apply` — fetch + write all surfaces. Default action.
  - `restore` — undo.
  - `status` — print current config + last apply timestamp + manifest head.
  - `config show|edit|validate` — config inspection.
  - `provider list|info <name>` — list available providers.
  - `qml-update-templates` — re-extract pristine QML templates from the
    running system (maintenance; requires root to read vendor files).
  - `doctor` — verify SDDM readability, font cache, template hashes,
    systemd timer status, and manifest health.
- Each subcommand has `--dry-run` (writes manifest but skips actual file
  mutation). `--dry-run` prints a human-readable table of planned ops
  including: source provider, resolved wallpaper path, config keys to write,
  root files to patch, and manifest entries to append.
- **Done means:** `trinity --help` reads cleanly and every command has a
  `--help`.

### Phase 9 — Migration helper + integration hardening (1 day)

- `trinity migrate-from-shell`:
  - Read `/usr/local/bin/bing-potd.sh` to detect the existing source.
  - Read `~/.config/kscreenlockerrc`, `appletsrc`, `theme.conf` to detect
    the current wallpaper paths.
  - Generate a starter `~/.config/trinity/config.toml`.
  - Do NOT delete anything. Print a checklist.
- `trinity doctor`:
  - Verify the shared wallpaper dir is world-readable.
  - Verify `fc-match Inter` resolves after font install.
  - Verify stored QML template hashes match the stripped on-disk files.
  - Verify the systemd timer is enabled and the manifest store exists.
- **Done means:** the user can run `trinity migrate-from-shell` on this
  box and get a valid config file, and `trinity doctor` exits 0 on a
  healthy install.

### Phase 10 — Hardening + docs (1 day)

- `docs/config-reference.md`: every key documented.
- `docs/migration-from-shell.md`: from-current-state to-package.
- `README.md`: 30-second quickstart.
- Bump version to 0.1.0. Tag. CHANGELOG entry.
- **Done means:** a fresh user can install, configure, and apply without
  touching shell.

## 10. Key technical decisions (locked in)

### 10.1 Font: Inter static .ttf, system-wide install (DECIDED)

`Inter` is open-source (OFL), ships as a static .ttf (~300 KB regular
weight), has complete Latin coverage, and is widely installed on
developer machines. Vendoring the .ttf avoids a network dependency at
install time.

`trinity install` copies the vendored `Inter-Regular.ttf` to
`/usr/local/share/fonts/trinity/Inter-Regular.ttf` and runs
`fc-cache -f`. This step requires root because the SDDM greeter runs as
the `sddm` user and cannot read fonts inside a regular user's home. If
the user declines sudo, the install continues but the login screen falls
back to the system default font; `trinity status` shows a warning.

If the user wants a different font later, the `surface.fonts.family`
config token takes any installed family name — `fc-match <name>` is
used to verify the family actually exists; warn if not.

### 10.2 QML patching strategy (DECIDED)

The current patched files live under `/usr/share/` and are owned by root.
There are two strategies:

**Strategy A — diff-based patch.** Keep a pristine copy of the
distro-shipped `Login.qml`, `MainBlock.qml`, `LockScreenUi.qml` in
`src/trinity/theme/templates/` (extracted at package build time or
shipped verbatim). At apply time, produce a unified diff against the
on-disk file. Apply the diff; if it fails, error out.

**Strategy B — direct edit with sentinel markers.** Write
`/* @trinity:start */ … /* @trinity:end */` blocks into the QML and
replace only the inside. Requires writing markers into distro files (we'd
need to patch them once).

**DECIDED:** **Option C — hybrid template sourcing.**

- At **build/package time**, ship a set of pristine QML templates in
  `src/trinity/theme/templates/` extracted from the developer's current
  Plasma version. These act as a safe fallback.
- At **`trinity install` time**, the CLI re-extracts fresh pristine
  templates from the running system and stores them in
  `~/.local/state/trinity/templates/`, preferring those over the shipped
  copies. This keeps the templates in sync with the installed Plasma
  version without requiring a new package release.
- At **apply time**, the backend uses the state-directory templates first,
  falling back to the shipped templates only if the state-directory copies
  are missing.
- **Strategy A** (diff against pristine template) is used for the very
  first patch. The very first successful patch writes Strategy B sentinel
  markers (`/* @trinity:start */` … `/* @trinity:end */`) into the same
  vendor files. All subsequent `trinity apply` operations use Strategy B
  to replace only the marked region. The `trinity restore` command removes
  the markers and rewrites the file back to the pristine template content.

**Template drift detection.** Before any patch, compute the SHA-256 of the
on-disk file with the marker region stripped out. Compare it to the stored
pristine template. If they differ:
1. Save the current file as `<path>.trinity.drift.<ts>`.
2. Re-extract a fresh pristine template from the running system and
   update `~/.local/state/trinity/templates/`.
3. If the re-extracted template still does not match the stripped on-disk
   file, emit a hard error and refuse to patch. Do not silently skip
   font/theme changes.

**Per-version templates.** For v1, templates are stored flat in the state
directory; drift detection is the primary mechanism for catching
incompatibility. If Plasma upgrades prove too noisy in practice, v2 may
introduce `templates/plasma-<major>.<minor>/` directories selected at
runtime. This is explicitly deferred.

The package cannot restart plasmalogin (it would log the user out).
Resolution: `trinity apply` always emits an info-level message:

> Wallpaper applied. To see it on the login screen, **log out** (the SDDM
> greeter caches theme files at startup).

This is documented in `docs/migration-from-shell.md` and shown once on
first apply.

### 10.3 Shared wallpaper path (DECIDED)

Keep `/usr/local/share/wallpapers/` as the plasmalogin-visible location.

- `trinity install --shared-dir` creates the directory as `root:root`
  0755. This requires sudo and is the only install-time root step besides
  the font copy.
- Each `trinity apply` writes `last_wallpaper.jpg` to that directory
  with mode `0644` and ownership matching the directory owner (normally
  `root:root`). If the user declines sudo for `install`, the directory is
  created under the running user with 0755 and the file is user-owned but
  world-readable (works on single-user systems, may fail plasmalogin
  visibility on multi-user systems).
- On a system where `/usr/local/` is read-only, fall back to
  `/var/cache/trinity/wallpapers/` and emit a clear warning. In that case
  `trinity install` must be run with root to ensure the plasmalogin
  user can traverse `/var/cache/trinity/`.
- The config token `surface.behaviour.shared_dir` is authoritative; the
  install step merely ensures it exists with sensible permissions.

### 10.4 Single source of truth for the shared wallpaper (DECIDED)

The per-user canonical copy is `~/.local/state/trinity/last_wallpaper.jpg`.
The plasmalogin-visible copy in `shared_dir` is a **copy**, not a hardlink.
Hardlinks save disk but break user-only wallpapers and complicate atomic
replacement across filesystem boundaries. Copy is the v1 default.

### 10.5 No shell out to `kwriteconfig6` for our own files

For files we own entirely (`manifest.jsonl`, config.toml), we write them
ourselves via `atomic.py`. For files we share with Plasma
(`appletsrc`, `kscreenlockerrc`), we use `kwriteconfig6` because Plasma
also writes them and we need to round-trip safely.

### 10.6 Logging

`structlog` → JSON → stdout → captured by systemd journal (`journalctl
--user -u trinity-pull.service`). No file logs unless the user opts
in via `[logging]` config.

## 11. Testing strategy

- **Unit:** pytest, all logic modules. Coverage target ≥ 80%.
- **Snapshot:** `test_qml_patch.py` uses inline snapshots of expected
  QML output. `test_drift.py` snapshots the stripped-template hash.
  Update via `pytest --snapshot-update` on intentional changes.
- **HTTP:** `respx` for httpx mocking. No real network in tests.
- **Integration:** an opt-in test that requires a real Plasma session
  (skip if `$DISPLAY`/`$WAYLAND_DISPLAY` unset). Marks: `@pytest.mark.integration`.
- **Golden runbook:** `tests/golden/apply-walkthrough.md` documents the
  expected end-to-end behaviour on a fresh VM. Read it before declaring
  Phase 5 done.
- **Security hardening tests:** verify downloaded images are decoded and
  mode `0644` is set before copying to `shared_dir`; verify third-party
  providers are loaded only from explicitly named entry points.

## 12. Distribution

| Channel | Command |
|---|---|
| From source (this dev) | `uv tool install /home/matt/Projects/background_manager` |
| From GitHub (future) | `uv tool install git+https://github.com/<user>/trinity.git` |
| From PyPI (future) | `uv tool install trinity` |
| Debian/Ubuntu (future, optional) | `packaging/deb/` produces a `.deb` for KDE Neon. Out of scope for v1. |

**No homebrew formula. No AUR. The target user is on KDE Neon.**

## 13. Migration plan (from the current shell-based setup)

This is what the user will do *after* the package is at v0.1.0:

1. `uv tool install /home/matt/Projects/background_manager`
2. `trinity migrate-from-shell --dry-run` (review what will be detected)
3. `trinity migrate-from-shell` (generates `~/.config/trinity/config.toml`)
   **Run this before any `apply` on a previously-patched system.** It
   captures the existing `.bak.*` QML files as the pristine baseline.
   Without it, `trinity` would diff against the already-patched files.
4. `trinity install` (installs font to `/usr/local/share/fonts/` and
   creates `/usr/local/share/wallpapers/`; both require sudo. Systemd
   user units do not require sudo.)
5. `trinity apply --dry-run` (review the planned file changes)
6. `trinity apply` (writes everything; logs the "log out to see login screen" hint)
7. Visual check: lock screen (Super+L), desktop (Alt+Tab to look at it).
8. Log out, back in. Check login screen.
9. `sudo rm /usr/local/bin/bing-potd.sh /usr/local/bin/bing-potd.sh.bak`
10. `systemctl --user disable --now bing-potd.timer`
11. `rm ~/.config/systemd/user/bing-potd.{service,timer}`

**Migration QML baseline.** The user's existing system already has hand-patched
`Login.qml`, `MainBlock.qml`, and `LockScreenUi.qml`, with `.bak.*` files next
to them. The migration helper must treat those `.bak.*` files as the pristine
templates, not the current patched files. If a `.bak.*` is missing, the helper
must copy the current patched file as the baseline and emit a warning that
`trinity restore` will restore to the current (already-modified) state, not to
vendor-original.

The migration helper is *additive only*. It does not delete anything.

## 14. Risks + open questions

### Known risks

| Risk | Mitigation |
|---|---|
| Plasma updates change `MainBlock.qml` / `LockScreenUi.qml` and our markers sit in a changed file | Template drift detection (§10.2): strip the sentinel region, hash the rest, compare to stored pristine template. Save drift backup, attempt re-extraction, and hard-fail if upstream divergence persists. Do not silently skip. |
| User installs multiple providers that conflict on `shared_dir` | The config schema enforces `shared_dir` is a single path; providers never write the file, only the orchestrator does. |
| Malicious or buggy third-party provider plugin | Only load plugins named explicitly in the config; reject unknown entry points at registry time. Document that third-party providers run as the invoking user and can write to `user_dir`. |
| httpx blocks on a slow network during the daily timer | 60-second timeout per request; service runs `Type=oneshot` so a timeout doesn't block subsequent runs. |
| Pillow can't decode a weird provider's PNG | Catch `UnidentifiedImageError`, log, skip the apply for that surface only. |
| plasmalogin user can't read the new wallpaper dir | Check at `install` time that the dir is world-readable; warn on systems where `/usr/local` is restricted. |

### Open questions for the user (asked one at a time when reached)

1. **Do we need a `trinity pause` to stop the timer temporarily?** A
   vacation mode. **Default:** not in v1.

**Closed questions (locked in):**

- **Font location:** `/usr/local/share/fonts/` with sudo, because the
  SDDM greeter runs as the `sddm` user and cannot read another user's
  home directory.
- **Shared wallpaper source of truth:** copy, not hardlink, from the
  per-user canonical file to the plasmalogin-visible shared file.
- **Inter vendoring:** vendored static `Inter-Regular.ttf` only, no
  variable font.

## 15. Out of scope, deferred to v2

- GUI / plasmoid
- Multi-monitor per-surface wallpaper sets (today we set the same image
  on all monitors; `appletsrc` already supports per-containment images)
- Theme variants beyond Breeze for SDDM
- PyPI release process (we ship via `uv tool install` from the repo for
  now)
- CI / GitHub Actions (this is a single-machine project)
- Telemetry / usage stats (NEVER)

---

## Appendix A — Why this isn't a single "wallpaper manager"

Existing tools (`variety`, `wallabag`, `nitrogen`) handle the *desktop*
only. None of them touch the lock screen or the login screen. The user
explicitly asked for a unified set. Treating wallpaper as a single
concept (a path on disk) misses that the three surfaces have different
config-file homes, different privilege contexts, and different update
mechanisms. The `SurfaceSet` data model in §8 reflects this.

## Appendix B — Why not just a Python rewrite of the shell scripts?

We are *not* rewriting `bing-potd.sh` line for line. The new package
restructures the problem:

| Old | New |
|---|---|
| One bash script that fetches and writes three config files inline | A composable pipeline: provider → orchestrator → backend writer |
| Provider logic mixed with file-mutation logic | Provider returns bytes; backend writes bytes |
| `curl` + manual JSON parsing | `httpx` + pydantic |
| Systemd units hand-written | Rendered from Jinja, versioned in the package |
| No tests | Snapshot + respx + integration |
| Hard-coded Bing | Pluggable providers |

The shell script got the user this far. It doesn't scale.

---

*End of plan. Implementation begins at Phase 0 in §9.*
