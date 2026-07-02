# Configuration Reference

The user configuration lives at `~/.config/usurface/config.toml` (or
`$XDG_CONFIG_HOME/usurface/config.toml`). It is strict: any unknown key
raises a validation error at load time.

## Top-level structure

```toml
[surface]            # required
schema_version = 1   # required

[surface.source]     # required
provider = "bing"
options = { ... }

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
user_dir   = "~/.local/state/usurface"
```

## `[surface.source]`

The provider plugin to fetch a wallpaper from. Built-in values:

| Name   | Description                              |
|--------|------------------------------------------|
| `bing` | Bing Picture of the Day (HTTP).          |
| `file` | A local image file (path in `options`).  |
| `solid`| Solid colour or 2-stop gradient.         |

`options` is a free-form table whose schema depends on the provider.
Validation is performed by the provider at fetch time.

## `[surface.fonts]`

Applied to login + lock QML via the sentinel-based patcher.

- `family`: any installed font family name. Verified with `fc-match` at
  install time.
- `weight`: a CSS-style weight token (e.g. `Normal`, `Bold`).
- `password_character`: the mask character shown in the password field.

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

- `on_idle_dim_seconds`: seconds before the lock screen dims. Rewrites
  the `fadeoutTimer` interval (seconds → milliseconds) in
  `LockScreenUi.qml`. A value of `0` sets `interval: 0` (the timer
  fires immediately, effectively never dimming). Evidence:
  `LockScreenUi.qml:164-166` `Timer { id: fadeoutTimer; interval: 10000 }`.
- `suppress_wake_keypress`: if true, the first keypress that wakes the
  locked screen should be consumed (not forwarded to the password
  field). **Not yet implemented** — the `Keys.onPressed` handler
  structure (`LockScreenUi.qml:160-163`) makes a safe in-place
  structural edit fragile; the field is accepted and validated but
  currently a documented no-op.

## `[surface.behaviour]`

File layout.

- `shared_dir`: directory visible to the SDDM user. Created by
  `usurface install` with root; the daily POTD is copied here.
- `user_dir`: per-user canonical copy directory. The latest wallpaper
  is always written here first; the shared copy follows.
