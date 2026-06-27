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
show_user_list = true

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

Tokens applied to the SDDM `Login.qml`.

- `clock_format`: passed to `Qt.formatDateTime`.
- `accent_color`: `#RGB` or `#RRGGBB`.
- `show_user_list`: whether to render the user list.

## `[surface.lock]`

Tokens applied to the Plasma lock screen.

- `on_idle_dim_seconds`: seconds before dimming the lock screen.
- `suppress_wake_keypress`: if true, consume the first keypress after
  wake so the user doesn't enter their password into the unlocked
  session.

## `[surface.behaviour]`

File layout.

- `shared_dir`: directory visible to the SDDM user. Created by
  `usurface install` with root; the daily POTD is copied here.
- `user_dir`: per-user canonical copy directory. The latest wallpaper
  is always written here first; the shared copy follows.
