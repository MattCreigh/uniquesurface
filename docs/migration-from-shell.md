# Migrating from the legacy shell-based setup

The legacy setup uses:

- `/usr/local/bin/bing-potd.sh` — a bash script that downloads Bing
  POTD and writes three config files.
- `~/.config/systemd/user/bing-potd.service` + `.timer` — a user-level
  systemd timer that runs the script once per day.
- A shared wallpaper at `/usr/local/share/wallpapers/last_wallpaper.jpg`
  that SDDM reads via its `theme.conf`.
- Hand-patched SDDM `Login.qml` and Plasma `MainBlock.qml` /
  `LockScreenUi.qml` to change the font and password character. The
  original files are kept as `*.bak.<timestamp>` next to each.

## Migration steps

1. **Install the package** (from this repo):

   ```sh
   uv tool install /home/matt/Projects/background_manager
   ```

2. **Generate a starter config** (additive only; does not delete
   anything):

   ```sh
   trinity migrate-from-shell --dry-run
   trinity migrate-from-shell
   ```

   This detects `bing-potd.sh` and the existing systemd timer, and
   writes `~/.config/trinity/config.toml`.

3. **Install the package** (this is the step that touches root-owned
   paths):

   ```sh
   sudo trinity install
   ```

   This copies the Inter font into `/usr/local/share/fonts/`, creates
   `/usr/local/share/wallpapers/` as `root:root 0755`, re-extracts the
   pristine QML templates from the system, writes the systemd user
   units, and enables the timer.

4. **Apply the configuration**:

   ```sh
   trinity apply --dry-run
   trinity apply
   ```

   The dry-run prints a table of every file that will be modified. The
   real run updates the desktop, lock, and login surfaces and appends
   entries to the manifest.

5. **Visual checks**:

   - Lock screen: press Super+L.
   - Desktop: visible immediately.
   - Login screen: log out and back in (SDDM caches theme files at
     startup).

6. **Remove the legacy pieces** (only after you have verified everything
   looks correct):

   ```sh
   sudo rm /usr/local/bin/bing-potd.sh /usr/local/bin/bing-potd.sh.bak
   systemctl --user disable --now bing-potd.timer
   rm ~/.config/systemd/user/bing-potd.service \
      ~/.config/systemd/user/bing-potd.timer
   ```

## Why the migration must be done before `apply`

The QML patcher uses a pristine template stored in
`~/.local/state/trinity/templates/` as the source of truth for drift
detection. If you run `trinity apply` on a system whose vendor QML
files are already hand-patched (without running `migrate-from-shell`
first), the patcher will compare the patched file against the freshly
extracted "pristine" template and treat the existing patches as drift.
This will fail the apply with a clear error message.

`migrate-from-shell` records the existing state and ensures that
`trinity install` re-extracts the actual vendor QML (not the patched
files) so the pristine baseline is correct.
