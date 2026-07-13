---
name: verify
description: Drive trinity end-to-end in an isolated sandbox to verify changes at the CLI surface — build/launch recipe, sandbox env, and the system paths that escape it.
---

# Verifying trinity changes

trinity is a CLI (`src/trinity/cli.py`, entry point `trinity`). Run the
working tree with `uv run trinity <cmd>` — no build step.

## Sandboxed end-to-end apply

Create an isolated environment so a real `apply` (live network is fine)
cannot touch the user's session:

```bash
SB=$(mktemp -d)
mkdir -p "$SB"/{cfg/trinity,state,shared,user}
cat > "$SB/cfg/trinity/config.toml" <<EOF
[surface]
schema_version = 1
[surface.source]
provider = "bing"            # or rss / solid / json-api / file
[surface.source.options]
mkt = "en-US"
[surface.theme_tokens]
enabled = false              # REQUIRED: omitting it auto-migrates to true
[surface.behaviour]
shared_dir = "$SB/shared"
user_dir = "$SB/user"
EOF

env XDG_CONFIG_HOME="$SB/cfg" XDG_STATE_HOME="$SB/state" \
    XDG_CACHE_HOME="$SB/cache" TRINITY_SHARED_DIR="$SB/shared" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent" \
    uv run trinity apply
```

- `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` makes the qdbus
  live-apply calls fail soft instead of repainting the real desktop.
- `kwriteconfig6` writes land in `$SB/cfg` (XDG-relative) — safe.
- Useful flows: `apply`, `apply --if-changed` (run twice: first
  converges via image digest, second skips via probe token; state in
  `$SB/user/refresh_state.json`), `apply --dry-run`, `provider list`.

## ⚠️ What the sandbox does NOT isolate

The **login backend uses hardcoded absolute paths**
(`/usr/share/sddm/themes/breeze/theme.conf.user`) and QML patching hits
real `/usr/share` vendor files. With `theme_tokens` enabled, the SDDM
fork also writes real system paths: `/usr/share/sddm/themes/trinity-breeze/`
and `/etc/sddm.conf.d/trinity.conf` (see `trinity.backends.sddm_fork`;
pytest redirects these via autouse conftest fixtures, the CLI sandbox
does not). On this machine those are **user-writable**, so a sandboxed
full `apply` WILL rewrite the real SDDM config. Either monkeypatch
`trinity.backends.login`'s `_THEME_CONF_USER_PATH`/`_THEME_CONF_PATH`
(and `sddm_fork`'s `FORK_THEME_DIR`/`DROPIN_PATH`), keep `theme_tokens`
disabled (the config above), or restore afterwards with:

```
# managed by trinity
background=/usr/local/share/wallpapers/last_wallpaper.jpg
color=#1d99f3
```

## Verifying systemd unit changes

Don't just render the template — user units can fail at *start* on this
distro (noble restricts capability-dropping directives). Probe a
directive with:

```bash
systemd-run --user --quiet --wait -p ProtectSystem=strict /bin/true
```

The real units live at `~/.config/systemd/user/trinity-pull.{service,timer}`
(regenerate: `trinity install --yes`; deployed tool: `uv tool install
--reinstall .`). Journal: `journalctl --user -u trinity-pull.service`.
