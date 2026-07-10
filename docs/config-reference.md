# Configuration Reference

The user configuration lives at `~/.config/trinity/config.toml` (or
`$XDG_CONFIG_HOME/trinity/config.toml`). It is strict: any unknown key
raises a validation error at load time.

## Top-level structure

```toml
[surface]            # required
schema_version = 1   # required

[surface.source]     # required
provider = "bing"
options = { ... }

[surface.theme_tokens]  # optional, defaults shown
enabled = false        # opt-in: enable QML patching and drift detection

[surface.fonts]      # optional, defaults shown
family = "Inter"
weight = "Normal"
password_character = "*"

[surface.login]      # optional, defaults shown
clock_format = "hh:mm"
accent_color = "#1d99f3"

[surface.lock]       # optional, defaults shown
on_idle_dim_seconds = 10
suppress_wake_keypress = true

[surface.behaviour]  # optional, defaults shown
shared_dir = "/usr/local/share/wallpapers"
user_dir   = "~/.local/state/trinity"
```

## `[surface.source]`

The provider plugin to fetch a wallpaper from. Built-in values:

| Name      | Description                                                       |
|-----------|-------------------------------------------------------------------|
| `bing`    | Bing Picture of the Day (HTTPS).                                  |
| `file`    | A local image file (path in `options`).                           |
| `solid`   | Solid colour or 2-stop gradient.                                  |
| `json-api`| Generic metadata-then-image recipe (HTTPS, JSON Pointer).         |

`options` is validated against the provider's pydantic schema at
config-load time. Each built-in provider declares a strict schema
(extra keys are rejected). See `trinity provider info <name>` for the
option table.

## `[surface.theme_tokens]`

Opt-in switch for the QML patching machinery.

- `enabled`: when `false` (the default), `apply` skips all QML patching
  and drift checks, and `install` skips template extraction. The
  `fonts`, `login`, and `lock` sections become inert — set `enabled = true`
  to use them. Pre-existing configs that don't declare this key are
  auto-migrated to `enabled = true` with a one-time deprecation log.

### `bing` options

| Key          | Type   | Default       | Notes                                   |
|--------------|--------|---------------|-----------------------------------------|
| `mkt`        | string | `"en-US"`     | Market code.                            |
| `resolution` | string | `"1920x1080"` | Requested resolution.                   |
| `index`      | int    | `0`           | Day offset (0 = today, 1 = yesterday).  |
| `timeout`    | float  | `30.0`        | Per-request timeout in seconds.         |

Downloads are capped at 50 MiB. The provider enforces HTTPS, IP-pinned
DNS resolution (private/loopback/link-local/reserved addresses are
rejected), and a 5-hop redirect cap.

### `json-api` options

Generic recipe for "GET a JSON metadata document, extract an image URL
with a JSON Pointer, then download the image." Most picture-of-the-day
APIs follow this shape.

| Key                | Type   | Default  | Notes                                                     |
|--------------------|--------|----------|-----------------------------------------------------------|
| `metadata_url`     | string | —        | Required. HTTPS only. Validated as `AnyHttpUrl` at load.  |
| `image_url_pointer`| string | —        | Required. RFC 6901 JSON Pointer (e.g. `/image/url`).      |
| `params`           | table  | `{}`     | Optional query string for the metadata request.           |
| `headers`          | table  | `{}`     | Optional HTTP headers (e.g. `User-Agent`).                |
| `timeout`          | float  | `30.0`   | Per-request timeout (0 < t ≤ 300).                        |

All the security guardrails from `bing` apply: HTTPS-only, IP-pinned
DNS, private/loopback rejection, 5-hop redirect cap, 5 MiB metadata
cap, 50 MiB image cap, header/param count and length caps.

Relative image URLs in the metadata are resolved against the metadata
URL, not the request origin.

**Example — Wikimedia Picture of the Day (no key required):**

```toml
[surface.source]
provider = "json-api"

[surface.source.options]
metadata_url     = "https://api.wikimedia.org/feed/v1/wikipedia/en/image/potd/featured/2026/07/10"
image_url_pointer = "/image/url"
```

**Example — NASA Astronomy Picture of the Day (DEMO_KEY rate-limited):**

```toml
[surface.source]
provider = "json-api"

[surface.source.options]
metadata_url     = "https://api.nasa.gov/planetary/apod"
image_url_pointer = "/url"
params           = { api_key = "DEMO_KEY", thumbs = "false" }
```

### `file` options

| Key    | Type   | Default | Notes                          |
|--------|--------|---------|--------------------------------|
| `path` | string | —       | Required. `~` and `$VAR` expand. |

For safety the path must resolve inside an allowed root:
`~/Pictures`, `~/Wallpapers`, `/usr/share/wallpapers`,
`/usr/share/backgrounds`, `/usr/local/share/wallpapers`, or the
directory named by `$TRINITY_SHARED_DIR`. Files over 100 MiB are
refused.

### `solid` options

| Key           | Type   | Default     | Notes                                |
|---------------|--------|-------------|--------------------------------------|
| `color`       | string | `"#1d99f3"` | `#RGB` or `#RRGGBB`.                 |
| `gradient_to` | string | (none)      | Second colour for a linear gradient. |
| `width`       | int    | `1920`      | 1–7680.                              |
| `height`      | int    | `1080`      | 1–7680.                              |
| `quality`     | int    | `85`        | JPEG quality (clamped to 1–100).     |

## `[surface.fonts]`

Applied to login + lock QML via the sentinel-based patcher.

- `family`: any installed font family name. Verified with `fc-match` at
  install time.
- `weight`: a Qt weight token (`Thin`, `ExtraLight`, `Light`, `Normal`,
  `Medium`, `DemiBold`, `Bold`, `ExtraBold`, `Black`) or a numeric
  weight `100`–`900`.
- `password_character`: the mask character shown in the password field
  (1–4 characters).

## `[surface.login]`

Tokens applied to the SDDM login screen.

- `clock_format`: passed to `Qt.formatDateTime` in the lock/login QML
  (rewritten in place where the vendor declares a `clockFormat`
  property).
- `accent_color`: `#RGB` or `#RRGGBB`. Written to the SDDM Breeze
  `theme.conf` `color=` key, which the theme reads in `Main.qml` as
  `config.color` → `sceneBackgroundColor` (the solid-background colour
  used when `type=color`, and the fallback behind image backgrounds).
  Evidence: `/usr/share/sddm/themes/breeze/theme.conf` `[General] color=`;
  `Main.qml:50`.

> **Removed:** `show_user_list` was removed because the SDDM Breeze
> theme computes user-list visibility from the user model
> (`userListModel.count` vs `disableAvatarsThreshold` in `Main.qml`)
> and exposes no `theme.conf` key or QML property to rewrite safely.
> Existing config files containing `show_user_list` still load — the
> key is stripped with a warning rather than failing validation.

## `[surface.lock]`

Tokens applied to the Plasma lock screen (`LockScreenUi.qml`).

- `on_idle_dim_seconds`: seconds before the lock screen dims (0–600).
  Rewrites the `fadeoutTimer` interval (seconds → milliseconds) in
  `LockScreenUi.qml`. A value of `0` sets `interval: 0`, which makes the
  timer fire immediately — the lock-screen UI dims as soon as it wakes.
  Use a positive value unless you want an always-dimmed lock screen.
  Evidence: `LockScreenUi.qml:164-166`
  `Timer { id: fadeoutTimer; interval: 10000 }`.
- `suppress_wake_keypress`: when `true` (the default), the keypress that
  wakes the lock screen is consumed instead of being typed into the
  password field. Implemented by inserting a guard into the password
  box's `Keys.onPressed` handler in `MainBlock.qml`; setting the key to
  `false` removes the guard on the next `apply`.

## `[surface.behaviour]`

File layout.

- `shared_dir`: directory visible to the SDDM user. Created by
  `trinity install` with root; the daily POTD is copied here.
- `user_dir`: per-user canonical copy directory. The latest wallpaper
  is always written here first; the shared copy follows.

## Environment variables

| Variable             | Effect                                                                 |
|----------------------|------------------------------------------------------------------------|
| `TRINITY_SHARED_DIR` | Overrides the shared wallpaper directory (also added to the `file` provider's allowed roots). |
| `TRINITY_DEBUG`      | When set, unexpected errors print a full Python traceback instead of the condensed error block. |
| `XDG_CONFIG_HOME` / `XDG_STATE_HOME` / `XDG_CACHE_HOME` | Standard XDG base-directory overrides for config, state, and cache locations. |
