# Override Mechanisms for SDDM and KDE Plasma 6 Lockscreen

**Status:** Research findings (Phase 5)
**Date:** 2026-07-10
**Author:** trinity research

---

## Executive summary

| Surface | Current mechanism | Sanctioned alternative | Recommendation |
|---|---|---|---|
| **SDDM login (wallpaper)** | Edit root-owned `/usr/share/sddm/themes/breeze/theme.conf` `background=` | Write `theme.conf.user` next to `theme.conf` — SDDM merges it over the base config | **Use `theme.conf.user`** (no vendor-file write for wallpaper-only) |
| **SDDM login (QML tokens)** | In-place patch `Login.qml` | Fork Breeze theme dir → `trinity-breeze/` → point `[Theme] Current=trinity-breeze` | **Fork the theme** for QML token users; `theme.conf.user` cannot patch QML |
| **Plasma lockscreen (QML tokens)** | In-place patch `MainBlock.qml` + `LockScreenUi.qml` | LNF package `contents/lockscreen/` override | **Keep descriptor+canary (Phase 4)** — LNF lockscreen is all-or-nothing and would fork the entire lockscreen UI for ~3 token edits |

The evidence below supports each of these conclusions.

---

## Topic 1: SDDM theme forking

### 1.1 Theme resolution precedence

SDDM's configuration system is layered. The precedence (lowest → highest) is:

1. **System config dir** `/usr/lib/sddm/sddm.conf.d/*.conf` — distro defaults
   (defined by `SYSTEM_CONFIG_DIR` in `CMakeLists.txt:189`)
2. **User config dir** `/etc/sddm.conf.d/*.conf` — admin drop-ins
   (defined by `CONFIG_DIR` in `CMakeLists.txt:184`)
3. **Main config file** `/etc/sddm.conf` — legacy single-file

Drop-ins are loaded in lexicographic order within each directory; later files
override earlier ones. The `Configuration::loadInternal()` implementation in
`src/common/ConfigReader.cpp` handles this merge.
**Source:** `sddm/sddm` `develop` branch, `src/common/Configuration.h:34-45`
(`Config(MainConfig, ...)` macro), `test/ConfigurationTest.cpp` (tests
`RightOnInitDir` at line 164 confirm the merge order).

The key entry for theme selection:

```cpp
// src/common/Configuration.h:47-48
Section(Theme,
    Entry(ThemeDir,  QString, _S(DATA_INSTALL_DIR "/themes"), ...);
    Entry(Current,   QString, _S(""),  _S("Current theme name"));
    ...
```

`[Theme] Current=<name>` selects which subdirectory under `ThemeDir`
(default `/usr/share/sddm/themes/`) is loaded. An empty value means "use
the embedded theme from Qt resources" (`Display::findGreeterTheme()` in
`src/daemon/Display.cpp:346-364`):

```cpp
QString Display::findGreeterTheme() const {
    QString themeName = mainConfig.Theme.Current.get();
    if (themeName.isEmpty())
        return QString();  // embedded qrc:/theme
    QDir dir(mainConfig.Theme.ThemeDir.get());
    if (dir.exists(themeName))
        return dir.absoluteFilePath(themeName);
    qWarning() << "The configured theme" << themeName
               << "doesn't exist, using the embedded theme instead";
    return QString();
}
```

### 1.2 `theme.conf` vs `theme.conf.user`

**`theme.conf.user` exists and is a sanctioned override mechanism.**

The theme config file name is read from `metadata.desktop`:

```cpp
// src/common/ThemeMetadata.cpp:57-65
void ThemeMetadata::setTo(const QString &path) {
    QSettings settings(path, QSettings::IniFormat);
    d->mainScript = settings.value("SddmGreeterTheme/MainScript", "Main.qml").toString();
    d->configFile = settings.value("SddmGreeterTheme/ConfigFile", "theme.conf").toString();
    ...
}
```

The Breeze theme's `metadata.desktop` declares `ConfigFile=theme.conf`.

The critical merge logic is in `ThemeConfig::setTo()`:

```cpp
// src/common/ThemeConfig.cpp:35-62
void ThemeConfig::setTo(const QString &path) {
    ...
    QSettings settings(path, QSettings::IniFormat);
    QSettings userSettings(path + QStringLiteral(".user"), QSettings::IniFormat);
    ...
    // read default keys
    for (const QString &key: settings.allKeys()) {
        insert(key, settings.value(key));
    }
    // read user set themes overwriting defaults if they exist
    for (const QString &key: userSettings.allKeys()) {
        if (!userSettings.value(key).toString().isEmpty()) {
            insert(key, userSettings.value(key));
        }
    }
    // if the main config contains a background, save this to a new config value
    // to themes can use it if the user set config background cannot be loaded
    if (settings.contains("background")) {
        insert("defaultBackground", settings.value("background"));
    }
}
```

**Key findings:**

- `theme.conf.user` is loaded from `<path>.user` — i.e. if `theme.conf` is at
  `/usr/share/sddm/themes/breeze/theme.conf`, then `theme.conf.user` is at
  `/usr/share/sddm/themes/breeze/theme.conf.user`.
- Every key in `theme.conf.user` **overrides** the corresponding key in
  `theme.conf` (the `insert()` call replaces the value).
- Empty values in `theme.conf.user` are **skipped** (`if (!userSettings.value(key).toString().isEmpty())`),
  so you can't blank out a key — you can only set/override it.
- The original `background` from `theme.conf` is preserved as `defaultBackground`
  for themes that want a fallback.
- The merged config is exposed to QML as the `config` context property
  (`GreeterApp::addViewForScreen()` sets
  `view->rootContext()->setContextProperty("config", m_themeConfig)`).

**This means:** for wallpaper-only SDDM customization, writing a
`theme.conf.user` file containing:

```ini
[General]
background=/usr/local/share/wallpapers/last_wallpaper.jpg
```

is a **sanctioned, non-destructive** override. The vendor `theme.conf` is
never touched. SDDM explicitly supports this pattern.

**Caveats:**
- `theme.conf.user` is in the same root-owned directory as `theme.conf`,
  so writing it still requires root/sudo.
- It is theme-specific (per-theme, not global).
- It cannot patch QML — only ini-config values exposed to the theme's QML
  via `config.<key>`.

### 1.3 Relative imports when forking the Breeze theme

The Breeze SDDM theme directory (`/usr/share/sddm/themes/breeze/`)
contains:

```
Background.qml       KeyboardButton.qml  Login.qml    SessionButton.qml  metadata.desktop  theme.conf
default-logo.svg     faces/              Main.qml     preview.png         translations/
```

The `metadata.desktop` declares `MainScript=Main.qml`. `Main.qml` is the
entry point; it imports `Login.qml` and other sibling files.

**QML relative imports:** SDDM's `GreeterApp::addViewForScreen()` loads the
main script from the theme path:

```cpp
// src/greeter/GreeterApp.cpp:192-196
QString mainScript = QStringLiteral("%1/%2").arg(m_themePath).arg(m_metadata->mainScript());
mainScriptUrl = QUrl::fromLocalFile(mainScript);
...
view->setSource(mainScriptUrl);
```

The QML engine resolves relative imports (`import "."`, `Qt.resolvedUrl(...)`)
relative to the **base URL of the loaded file**. Since `m_themePath` is the
forked directory, all relative imports resolve within the forked dir —
**they do NOT break**.

**Image paths in `theme.conf`:** The `background=` key is an absolute path
in the current setup (`/usr/local/share/wallpapers/...`). Other image paths
in `theme.conf` (e.g. `logo=`) are relative to the theme directory. Copying
the whole directory preserves these relative paths.

**`import org.kde.breeze.components`:** The Breeze `Login.qml` imports
`org.kde.breeze.components` — this is a **QML module** registered via
`qmldir` in the theme's `Main.qml` (or via a `qmldir` file), not a relative
path. It resolves via the QML import path (`GreeterApp` adds
`IMPORTS_INSTALL_DIR` to the engine's import path at line 166 of
`GreeterApp.cpp`). **This works regardless of the theme directory name** —
the import is by module URI, not by path.

**Conclusion:** Copying `/usr/share/sddm/themes/breeze/` →
`/usr/share/sddm/themes/trinity-breeze/` preserves all internal relative
imports. The only change needed is updating `metadata.desktop`'s `Name=`
and `Theme-Id=` to avoid confusion, and editing the QML files in the fork
for token patches.

**To activate:** Add a drop-in `/etc/sddm.conf.d/trinity.conf`:

```ini
[Theme]
Current=trinity-breeze
```

Or set it in the main config. SDDM reads `Current=` from the merged config.

### 1.4 Sources (Topic 1)

| Claim | Source file | URL |
|---|---|---|
| `theme.conf.user` merge | `src/common/ThemeConfig.cpp:35-62` | https://github.com/sddm/sddm/blob/develop/src/common/ThemeConfig.cpp |
| ConfigFile from metadata | `src/common/ThemeMetadata.cpp:57-65` | https://github.com/sddm/sddm/blob/develop/src/common/ThemeMetadata.cpp |
| Theme selection (`Current=`) | `src/daemon/Display.cpp:346-364` | https://github.com/sddm/sddm/blob/develop/src/daemon/Display.cpp |
| Config entry definitions | `src/common/Configuration.h:45-56` | https://github.com/sddm/sddm/blob/develop/src/common/Configuration.h |
| Config dir paths | `CMakeLists.txt:184-189` | https://github.com/sddm/sddm/blob/develop/CMakeLists.txt |
| Greeter loads theme from path | `src/greeter/GreeterApp.cpp:96-137, 192-196` | https://github.com/sddm/sddm/blob/develop/src/greeter/GreeterApp.cpp |
| Config drop-in merge order | `test/ConfigurationTest.cpp:164-182` | https://github.com/sddm/sddm/blob/develop/test/ConfigurationTest.cpp |

---

## Topic 2: Look-and-feel packages for lockscreen override

### 2.1 Does LNF override the lockscreen QML?

**No — the lockscreen QML comes from the `Plasma/Shell` package, not the
LNF package.**

The kscreenlocker greeter (`kscreenlocker_greet`) loads the lockscreen QML
via `UnlockApp::setShell()`:

```cpp
// kscreenlocker/greeter/greeterapp.cpp:504-517
void UnlockApp::setShell(const QString &shell)
{
    m_packageName = shell;
    KPackage::Package package = KPackage::PackageLoader::self()
        ->loadPackage(QStringLiteral("Plasma/Shell"));  // <-- Shell, not LookAndFeel

    if (!m_packageName.isEmpty()) {
        package.setPath(m_packageName);
    }
    if (!verifyPackageApi(package)) {
        qCWarning(KSCREENLOCKER_GREET) << "Lockscreen QML outdated, falling back to default";
        package.setPath(QStringLiteral("org.kde.plasma.desktop"));
    }
    m_mainQmlPath = package.fileUrl("lockscreenmainscript");  // <-- from Shell package
    m_shellIntegration->setPackage(package);
    ...
}
```

The `lockscreenmainscript` file definition is in the **`Plasma/Shell`**
package structure (`libplasma/src/plasma/packagestructure/shell/shellpackage.cpp`):

```cpp
// KDE/libplasma: src/plasma/packagestructure/shell/shellpackage.cpp
// Lock screen
package->addDirectoryDefinition("lockscreen", QStringLiteral("lockscreen"));
package->addFileDefinition("lockscreenmainscript", QStringLiteral("lockscreen/LockScreen.qml"));
```

The `Plasma/Shell` package root is `plasma/shells/` — i.e.
`/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/`.

### 2.2 LNF package structure (does NOT include lockscreen)

The `Plasma/LookAndFeel` package structure is defined in
`plasma-workspace/shell/packageplugins/lookandfeel/lookandfeel.cpp`:

```cpp
// plasma-workspace: shell/packageplugins/lookandfeel/lookandfeel.cpp
void initPackage(KPackage::Package *package) override
{
    package->setDefaultPackageRoot(QStringLiteral("plasma/look-and-feel/"));
    package->removeDefinition("mainscript");
    package->addFileDefinition("defaults", "defaults");
    package->addFileDefinition("layoutdefaults", "layouts/defaults");
    package->addDirectoryDefinition("plasmoidsetupscripts", "plasmoidsetupscripts");
    package->addFileDefinition("colors", "colors");
    package->addDirectoryDefinition("previews", "previews");
    package->addFileDefinition("preview", "previews/preview.png");
    package->addFileDefinition("fullscreenpreview", "previews/fullscreenpreview.jpg");
    package->addFileDefinition("lockscreenpreview", "previews/lockscreen.png");
    package->addFileDefinition("splashpreview", "previews/splash.png");
    package->addFileDefinition("windowswitcherpreview", "previews/windowswitcher.png");
    package->addDirectoryDefinition("logout", "logout");
    package->addFileDefinition("logoutmainscript", "logout/Logout.qml");
    package->addDirectoryDefinition("splash", "splash");
    package->addFileDefinition("splashmainscript", "splash/Splash.qml");
    package->addDirectoryDefinition("windowswitcher", "windowswitcher");
    package->addFileDefinition("windowswitchermainscript", "windowswitcher/WindowSwitcher.qml");
    package->addDirectoryDefinition("layouts", "layouts");
    package->setPath(DEFAULT_LOOKANDFEEL);  // org.kde.breeze.desktop
}
```

**There is NO `lockscreen` directory or `lockscreenmainscript` definition in
the LNF package.** The only lockscreen-related entry is
`lockscreenpreview` (a preview image), not the actual QML.

The KDE Community Wiki page for [Plasma/lookAndFeelPackage](https://community.kde.org/Plasma/lookAndFeelPackage)
lists a `lockscreen/` directory in the documented structure, but the
**actual source code** (`lookandfeel.cpp`) does **not** define it. This
appears to be stale documentation from an older Plasma version where LNF
could override the lockscreen. In Plasma 6, the lockscreen is exclusively
sourced from the `Plasma/Shell` package.

**Empirical confirmation on this system:**
```
$ ls /usr/share/plasma/look-and-feel/org.kde.breeze.desktop/contents/
defaults  layouts  logout  previews  splash
```
No `lockscreen/` directory in the Breeze LNF package. The lockscreen QML
lives in the shell package:
```
$ ls /usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/
config.qml  config.xml  LockOsd.qml  LockScreen.qml  LockScreenUi.qml
MainBlock.qml  MediaControls.qml  NoPasswordUnlock.qml  PasswordSync.qml  qmldir
```

### 2.3 Shell package selection (how the lockscreen is chosen)

The shell package is selected by `ShellIntegration::defaultShell()`:

```cpp
// kscreenlocker/settings/shell_integration.cpp
QString ShellIntegration::defaultShell() const
{
    KSharedConfig::Ptr startupConf = KSharedConfig::openConfig("plasmashellrc");
    KConfigGroup startupConfGroup(startupConf, "Shell");
    const QString defaultValue = qEnvironmentVariable(
        "PLASMA_DEFAULT_SHELL", "org.kde.plasma.desktop");
    QString value = startupConfGroup.readEntry("ShellPackage", defaultValue);
    return value.isEmpty() ? defaultValue : value;
}
```

**The lockscreen shell is selected by `plasmashellrc [Shell] ShellPackage`.**
This is the **Plasma Shell** package (e.g. `org.kde.plasma.desktop`), not
the LNF. The LNF (`kdeglobals [KDE] widgetStyle` / `plasma-apply-lookandfeel`)
controls colors, splash, logout, window switcher — **not** the lockscreen.

To override the lockscreen, you would need to either:
1. Create a custom `Plasma/Shell` package (e.g. `org.kde.trinity.desktop`)
   with a `contents/lockscreen/` directory, and set
   `plasmashellrc [Shell] ShellPackage=org.kde.trinity.desktop`.
2. Or patch the existing shell package's `contents/lockscreen/*.qml` in place
   (what trinity currently does).

### 2.4 Partial override (only MainBlock.qml)

**There is no partial fallback mechanism.** The kscreenlocker greeter loads
a single file: `package.fileUrl("lockscreenmainscript")` →
`lockscreen/LockScreen.qml`. That file then imports/loads the rest
(`LockScreenUi.qml`, `MainBlock.qml`, etc.) via relative QML imports.

The `verifyPackageApi()` check confirms whether the package provides its
own lockscreen or falls back:

```cpp
// kscreenlocker/greeter/greeterapp.cpp:78-95
bool verifyPackageApi(const KPackage::Package &package)
{
    if (package.metadata().value("X-Plasma-APIVersion", "1").toInt() >= 2) {
        return true;
    }
    if (!package.filePath("lockscreenmainscript").contains(package.path())) {
        // The current package does not contain the lock screen and we are
        // using the fallback package. So check to see if that package has
        // the right version instead.
        if (package.fallbackPackage().metadata().value("X-Plasma-APIVersion", "1").toInt() >= 2) {
            return true;
        }
    }
    return false;
}
```

If the shell package doesn't contain `lockscreen/LockScreen.qml`,
KPackage falls back to the fallback package (Breeze desktop). But this is
**all-or-nothing** — you either provide the entire `lockscreen/` directory
or you get the fallback's entire `lockscreen/` directory. There is no
mechanism to override individual files (e.g. just `MainBlock.qml`) while
inheriting the rest from the fallback.

**Putting only `MainBlock.qml` in a custom shell package's
`contents/lockscreen/` would NOT work** — `LockScreen.qml` (the main
script) would be missing, the package would be invalid, and kscreenlocker
would fall back to the embedded fallback theme (blue rectangle, the
`qrc:/fallbacktheme/LockScreen.qml`).

### 2.5 Active LNF / shell selection

- **LNF selection:** `plasma-apply-lookandfeel --apply <name>` writes to
  `~/.config/lookandfeelrc` (and applies colors, splash, etc.). Current:
  `kdeglobals [KDE] widgetStyle=Breeze`. No `lookandfeelrc` on this system.
- **Shell selection:** `plasmashellrc [Shell] ShellPackage` (empty on this
  system → defaults to `org.kde.plasma.desktop`).
- **Lockscreen config:** `kscreenlockerrc` — the `themePluginId` key is
  empty on this system. Per the repo architecture notes, `themePluginId`
  in `kscreenlockerrc` is the LNF for colors/icons only, **not** for
  lockscreen QML override.

### 2.6 Sources (Topic 2)

| Claim | Source file | URL |
|---|---|---|
| Lockscreen from `Plasma/Shell` not LNF | `kscreenlocker/greeter/greeterapp.cpp:504-517` | https://github.com/KDE/kscreenlocker/blob/master/greeter/greeterapp.cpp |
| `lockscreenmainscript` in Shell package | `libplasma/src/plasma/packagestructure/shell/shellpackage.cpp` | https://github.com/KDE/libplasma/blob/master/src/plasma/packagestructure/shell/shellpackage.cpp |
| LNF package has no lockscreen dir | `plasma-workspace/shell/packageplugins/lookandfeel/lookandfeel.cpp` | https://github.com/KDE/plasma-workspace/blob/master/shell/packageplugins/lookandfeel/lookandfeel.cpp |
| Shell selection via `plasmashellrc` | `kscreenlocker/settings/shell_integration.cpp` (`defaultShell()`) | https://github.com/KDE/kscreenlocker/blob/master/settings/shell_integration.cpp |
| `verifyPackageApi` fallback logic | `kscreenlocker/greeter/greeterapp.cpp:78-95` | https://github.com/KDE/kscreenlocker/blob/master/greeter/greeterapp.cpp |
| Fallback to built-in on QML error | `kscreenlocker/greeter/greeterapp.cpp:356-371` | https://github.com/KDE/kscreenlocker/blob/master/greeter/greeterapp.cpp |
| LNF wiki (stale re: lockscreen) | KDE Community Wiki | https://community.kde.org/Plasma/lookAndFeelPackage |

---

## Topic 3: Trade-off analysis

### 3.1 Is LNF lockscreen override all-or-nothing?

**Yes, and it's worse than all-or-nothing — LNF can't override the lockscreen
at all in Plasma 6.**

The LNF package structure (`lookandfeel.cpp`) does not define a
`lockscreen` directory or `lockscreenmainscript`. The lockscreen QML is
sourced exclusively from the `Plasma/Shell` package
(`shellpackage.cpp`). To override the lockscreen, you must create a full
custom `Plasma/Shell` package.

Even if you did create a custom shell package, the override is
**all-or-nothing**: you must provide the complete `contents/lockscreen/`
file set (`LockScreen.qml`, `LockScreenUi.qml`, `MainBlock.qml`,
`MediaControls.qml`, `NoPasswordUnlock.qml`, `PasswordSync.qml`,
`LockOsd.qml`, `config.qml`, `config.xml`, `qmldir`). Missing the main
script (`LockScreen.qml`) causes the package to be invalid and kscreenlocker
falls back to the built-in blue-rectangle fallback.

This means:
- Forking the lockscreen = maintaining a **full copy** of ~10 QML files
- Every upstream Plasma update to any of those files must be manually merged
- The forked lockscreen diverges from Breeze over time
- A single stale file can break the lockscreen (user gets locked out →
  blue fallback → must TTY-recover)

For trinity's use case — editing ~3 properties (`fontFamily`, `passwordCharacter`,
`clockFormat`) in `MainBlock.qml` and adding a `consumeNextKey` guard in
`LockScreenUi.qml` — forking the entire lockscreen file set is a **massive
maintenance burden** for a tiny customization.

### 3.2 Is descriptor+canary (Phase 4) a better trade-off for the lockscreen?

**Yes.** The Phase 4 descriptor+canary approach is the better trade-off for
the lockscreen:

| Factor | Descriptor+canary (Phase 4) | Full shell package fork |
|---|---|---|
| Files touched | 2 QML files, in-place patch | ~10 QML files, full copy |
| Vendor file writes | Yes (root-owned) | No (own directory) |
| Upstream tracking | Canary CI detects anchor drift | Manual diff every release |
| Breakage risk | qmllint validation + rollback | Stale files → lockout |
| Reversibility | Manifest-based restore | Delete fork dir + unset `ShellPackage` |
| Maintenance | Update descriptor TOML on Plasma version change | Re-merge all 10 files on every Plasma update |
| Scope of change | Only the ~3 properties we care about | Entire lockscreen UI forked |

The descriptor+canary approach:
- Patches **only** the specific properties we manage (font, password char,
  clock format, wake-keypress guard).
- Validates with qmllint after patching; rolls back on failure.
- Has a canary CI that fetches upstream QML weekly and asserts anchors still
  match — so we know **before** a release if Plasma changed the QML.
- Uses TOML descriptors per Plasma version, so a new layout = new descriptor
  file, no code change.
- Is reversible via the manifest (restores pristine bytes).

The only downside is the vendor-file write (root-owned
`/usr/share/plasma/shells/...`). But this is a **surgical** edit of 2 files
with validation and rollback, versus a **full fork** of 10+ files with no
validation and manual merge burden.

### 3.3 Should SDDM `theme.conf.user` avoid vendor-file writes for wallpaper-only?

**Yes.** `theme.conf.user` is a sanctioned SDDM mechanism (Topic 1.2) that
merges over `theme.conf` without touching it. For wallpaper-only SDDM
customization (no QML token patches), writing `theme.conf.user` is strictly
better than editing `theme.conf`:

- No vendor file is modified — `theme.conf` stays pristine.
- The override is self-contained in one file (`theme.conf.user`).
- Reversal = delete `theme.conf.user`.
- The `defaultBackground` mechanism preserves the original background as
  a fallback if the user-set background can't be loaded.

**However:** for QML token patches (font, password character), `theme.conf.user`
cannot help — it only overrides ini-config values, not QML source. QML token
patches require either:
- In-place patching of `Login.qml` (current approach, vendor-file write), or
- Forking the Breeze theme directory (Topic 1.3) and editing QML in the fork.

### 3.4 Recommendation

| Surface | Mechanism | Rationale |
|---|---|---|
| **SDDM wallpaper** | `theme.conf.user` | Sanctioned, non-destructive, no vendor-file write |
| **SDDM QML tokens** | Fork Breeze → `trinity-breeze/` + `[Theme] Current=` | Avoids vendor-file writes; fork is self-contained; relative imports survive |
| **Lockscreen QML tokens** | Keep descriptor+canary (Phase 4) | LNF can't override lockscreen; full shell fork is disproportionate; canary CI provides upstream drift detection |

**Implementation plan for Phase 5:**

1. **SDDM wallpaper:** Replace the `theme.conf` editor with a
   `theme.conf.user` writer. The file lives at
   `/usr/share/sddm/themes/breeze/theme.conf.user` (root-owned). Content:
   ```ini
   [General]
   background=<wallpaper_path>
   ```
   Register in manifest for reversal. This is a one-file write, no vendor
   file touched.

2. **SDDM QML tokens:** Implement a theme fork path:
   - Copy `/usr/share/sddm/themes/breeze/` →
     `/usr/share/sddm/themes/trinity-breeze/` (if not already present).
   - Update `metadata.desktop` `Name=Trinity Breeze` and
     `Theme-Id=trinity-breeze`.
   - Apply QML token patches to the forked `Login.qml` (same descriptor
     mechanism as the lockscreen).
   - Write `/etc/sddm.conf.d/trinity.conf` with `[Theme] Current=trinity-breeze`.
   - Register all writes in manifest for reversal.
   - This is only done if QML token customization is requested (non-default
     font/password char). Wallpaper-only users get just `theme.conf.user`.

3. **Lockscreen QML tokens:** Keep the Phase 4 descriptor+canary approach.
   No change needed. The canary CI already detects upstream drift. The
   in-place patch is surgical (2 files), validated by qmllint, and reversible
   via manifest. A full shell package fork would be disproportionate.

4. **Canary CI extension:** Add the SDDM `Login.qml` anchors to the canary
   test (if not already covered) so upstream SDDM/Breeze QML drift is
   detected for the forked theme path too.

---

## Manual validation checklist template

Use this checklist to validate any override mechanism change end-to-end.

### Pre-apply

- [ ] Record current state:
  ```bash
  # SDDM
  ls /usr/share/sddm/themes/
  cat /etc/sddm.conf.d/*.conf 2>/dev/null
  head -5 /usr/share/sddm/themes/breeze/theme.conf
  ls /usr/share/sddm/themes/breeze/theme.conf.user 2>/dev/null || echo "no theme.conf.user"

  # Lockscreen
  kreadconfig6 --file plasmashellrc --group Shell --key ShellPackage
  ls /usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/

  # LNF
  ls /usr/share/plasma/look-and-feel/
  cat ~/.config/lookandfeelrc 2>/dev/null
  ```

- [ ] Back up files that will be touched:
  ```bash
  sudo cp /usr/share/sddm/themes/breeze/theme.conf \
     /usr/share/sddm/themes/breeze/theme.conf.pre-trinity
  ```

### Apply

- [ ] Run `sudo trinity apply` (or the specific apply command under test)
- [ ] Inspect the fork/override:
  ```bash
  # theme.conf.user (wallpaper-only path)
  cat /usr/share/sddm/themes/breeze/theme.conf.user

  # OR forked theme (QML token path)
  ls /usr/share/sddm/themes/trinity-breeze/
  diff /usr/share/sddm/themes/breeze/Login.qml \
       /usr/share/sddm/themes/trinity-breeze/Login.qml
  cat /etc/sddm.conf.d/trinity.conf

  # Lockscreen (descriptor path)
  diff /usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/MainBlock.qml \
       /usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/MainBlock.qml.bak
  ```

### Restart DM / trigger lockscreen

- [ ] Restart SDDM (WARNING: this logs you out):
  ```bash
  sudo systemctl restart sddm
  ```
  Or test the greeter without restarting:
  ```bash
  # SDDM greeter test mode (if available)
  sddm-greeter --test-mode --theme /usr/share/sddm/themes/trinity-breeze 2>&1 | head -20
  ```

- [ ] Test the lockscreen greeter:
  ```bash
  timeout 3 /usr/lib/x86_64-linux-gnu/libexec/kscreenlocker_greet --testing
  # Clean load = "Locked at <ts>" + "Terminated"
  # Broken = "Failed to load lockscreen QML, falling back to built-in locker"
  ```

### Verify

- [ ] SDDM login screen shows the correct wallpaper and font
- [ ] Lock screen shows the correct wallpaper, font, and password character
- [ ] Lock screen wake-keypress guard works (type a letter → should not
      appear in password field)
- [ ] No "falling back to built-in locker" in journal:
  ```bash
  journalctl --user -b | grep -i "falling back\|failed to load" | grep -i lock
  ```

### Restore

- [ ] Run `trinity restore`
- [ ] Verify restoration:
  ```bash
  # SDDM
  ls /usr/share/sddm/themes/breeze/theme.conf.user 2>/dev/null && echo "STILL EXISTS - restore failed" || echo "removed"
  ls /usr/share/sddm/themes/trinity-breeze/ 2>/dev/null && echo "STILL EXISTS - restore failed" || echo "removed"
  cat /etc/sddm.conf.d/trinity.conf 2>/dev/null && echo "STILL EXISTS - restore failed" || echo "removed"

  # Lockscreen
  diff /usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/MainBlock.qml \
       /usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/MainBlock.qml.bak
  ```

- [ ] Restart SDDM and confirm clean Breeze defaults:
  ```bash
  sudo systemctl restart sddm
  ```