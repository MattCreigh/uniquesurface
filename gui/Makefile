.PHONY: build test vet clean install uninstall run

# KDE Neon / Ubuntu 24.04 ships only webkit2gtk-4.1, but webview_go's cgo
# directive asks for webkit2gtk-4.0. The shim in packaging/pkgconfig
# delegates 4.0 -> 4.1 (they are API-compatible for webview's usage), so
# the build works on a stock noble box with only the 4.1 -dev package.
# Build deps: golang-go libwebkit2gtk-4.1-dev libgtk-3-dev (gcc/pkg-config).
PKGCONF := $(CURDIR)/packaging/pkgconfig
export PKG_CONFIG_PATH := $(PKGCONF):$(shell pkg-config --variable pc_path pkg-config)

PREFIX ?= $(HOME)/.local
BINDIR := $(PREFIX)/bin
APPDIR := $(HOME)/.local/share/applications

build:
	go build -o trinity-gui .

test:
	go test ./...

vet:
	go vet ./...

install: build
	install -Dm755 trinity-gui $(BINDIR)/trinity-gui
	mkdir -p $(APPDIR)
	printf '%s\n' \
	  '[Desktop Entry]' \
	  'Type=Application' \
	  'Version=1.0' \
	  'Name=Trinity Wallpaper Manager' \
	  'GenericName=Wallpaper Manager' \
	  'Comment=Manage desktop, lock, and login wallpapers' \
	  'Exec=$(BINDIR)/trinity-gui' \
	  'Icon=preferences-desktop-wallpaper' \
	  'Terminal=false' \
	  'Categories=Settings;DesktopSettings;' \
	  'Keywords=wallpaper;background;trinity;bing;lockscreen;' \
	  'StartupNotify=true' \
	  'StartupWMClass=trinity-gui' \
	  > $(APPDIR)/trinity-gui.desktop
	-update-desktop-database $(APPDIR) 2>/dev/null
	@echo "Installed trinity-gui -> $(BINDIR); launcher -> $(APPDIR)"

uninstall:
	rm -f $(BINDIR)/trinity-gui $(APPDIR)/trinity-gui.desktop
	-update-desktop-database $(APPDIR) 2>/dev/null

clean:
	rm -f trinity-gui

run: build
	./trinity-gui
