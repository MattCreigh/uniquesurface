"""Click CLI entry point for trinity.

Exit-code convention (kept deliberately small):

- ``0`` — success.
- ``1`` — operation failed (network error, backend failure, invalid
  config content, health-check failure from ``doctor``).
- ``2`` — precondition/usage problem the user must resolve first
  (missing config, refusing to overwrite an existing file, unknown
  provider name). Matches Click's own usage-error exit code.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from types import TracebackType

import click

from trinity import __version__
from trinity.logging import configure_logging, get_logger

_log = get_logger(__name__)


class CLIError(RuntimeError):
    """A user-facing error with an optional hint.

    The top-level ``main`` group catches this and prints the message and
    hint to stderr, then exits with a non-zero status. This is how
    graceful failures are signalled from anywhere in the call stack.
    """

    def __init__(
        self, message: str, *, hint: str | None = None, status: int = 1
    ) -> None:
        super().__init__(message)
        self.hint = hint
        self.status = status


def _format_error(message: str, hint: str | None) -> str:
    """Render a user-facing error block, including a hint if present."""
    lines = [f"error: {message}"]
    if hint:
        for h in hint.splitlines():
            lines.append(f"  {h}")
    return "\n".join(lines)


@click.group(invoke_without_command=True)
@click.option("--version", is_flag=True, help="Print version and exit.")
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    help="Log level.",
)
@click.pass_context
def main(ctx: click.Context, version: bool, log_level: str) -> None:
    """trinity — Unified Plasma 6 surface set manager."""
    configure_logging(log_level)
    if version or ctx.invoked_subcommand is None:
        click.echo(f"trinity {__version__}")


def _install_excepthook() -> None:
    """Install a top-level error handler so unexpected exceptions show a
    clean message instead of a Python traceback.

    The user can opt back into the traceback with ``TRINITY_DEBUG=1``.
    """

    def excepthook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        if os.environ.get("TRINITY_DEBUG"):
            traceback.print_exception(exc_type, exc_value, exc_tb)
            return
        if issubclass(exc_type, KeyboardInterrupt):
            click.echo("aborted.", err=True)
            return
        if isinstance(exc_value, CLIError):
            click.echo(_format_error(str(exc_value), exc_value.hint), err=True)
            sys.exit(exc_value.status)
        click.echo(
            _format_error(
                f"unexpected error: {exc_value}",
                "set TRINITY_DEBUG=1 for a full traceback",
            ),
            err=True,
        )
        sys.exit(1)

    sys.excepthook = excepthook


# --- apply -------------------------------------------------------------


@main.command()
@click.option("--dry-run", is_flag=True, help="Print the plan without writing.")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to config file (default: ~/.config/trinity/config.toml).",
)
@click.option(
    "--adopt-drift",
    is_flag=True,
    help=(
        "Adopt drifted vendor QML as the new pristine baseline (after a Plasma update)."
    ),
)
def apply(dry_run: bool, config_path: Path | None, adopt_drift: bool) -> None:
    """Apply the configured wallpaper to desktop, lock, and login."""
    from trinity import paths
    from trinity.config import load_config
    from trinity.manifest import Manifest
    from trinity.orchestrator import apply_to_surfaces

    if config_path is None and not paths.config_file().exists():
        raise CLIError(
            f"no config at {paths.config_file()}",
            hint="run `trinity config init` to create one",
            status=2,
        )

    from pydantic import ValidationError

    try:
        cfg = load_config(config_path)
    except (ValidationError, ValueError, OSError) as exc:
        raise CLIError(
            f"invalid config {config_path or paths.config_file()}: {exc}",
            hint="run `trinity config validate` after fixing the file",
        ) from exc

    from trinity.theme.font_install import is_installed

    if not is_installed(cfg.surface.fonts.family):
        click.echo(
            f"Warning: font family '{cfg.surface.fonts.family}' "
            "not found by fontconfig.",
            err=True,
        )

    # Pre-flight: if we are not root and the login surface (SDDM theme)
    # is present, the login backend will fail. Warn the user clearly
    # but continue so the user-mode surfaces still get updated.
    from trinity.backends.login import login_surface_needs_root

    if not dry_run and login_surface_needs_root():
        click.echo(
            "Note: login (SDDM) surface requires root; "
            "that step will be skipped or fail unless you re-run with sudo.",
            err=True,
        )

    manifest = Manifest()
    plan = apply_to_surfaces(
        cfg, manifest=manifest, dry_run=dry_run, adopt_drift=adopt_drift
    )
    for line in plan:
        click.echo(line)
    # The orchestrator already emits a precise "restart <dm>" or
    # "log out fully" hint when the login surface was actually updated,
    # so we only add a generic note when nothing login-related was said.
    if not dry_run and not any("login wallpaper updated" in line for line in plan):
        if not any("login" in line.lower() for line in plan):
            click.echo(
                "To see the new wallpaper on the SDDM login screen, "
                "log out fully (not switch-user)."
            )


# --- restore -----------------------------------------------------------


@main.command()
@click.option(
    "--to",
    "to_timestamp",
    default=None,
    help="Stop restoring at this ISO timestamp.",
)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def restore(to_timestamp: str | None, yes: bool) -> None:
    """Revert every recorded change."""
    from trinity.manifest import Manifest
    from trinity.manifest import restore as _restore

    m = Manifest()
    if not m.path.exists():
        click.echo("manifest log is empty; nothing to restore.")
        return
    if not yes and not click.confirm(
        f"Restore {len(m.iter_entries())} recorded change(s)?"
    ):
        click.echo("aborted.")
        return
    count = _restore(m, to=to_timestamp)
    click.echo(f"restored {count} file(s).")


# --- status ------------------------------------------------------------


@main.command()
def status() -> None:
    """Show the current configuration and last apply status."""
    from trinity import paths, systemd
    from trinity.manifest import Manifest
    from trinity.theme import drift, extract

    cfg = paths.config_file()
    click.echo(f"config: {cfg} {'(present)' if cfg.exists() else '(missing)'}")
    m = Manifest()
    head = m.head(5)
    click.echo(f"manifest entries: {len(m.iter_entries())} (showing last {len(head)})")
    for entry in head:
        click.echo(f"  {entry.ts} {entry.op:6s} {entry.path}")
    from trinity.config import load_config as _load_config
    from trinity.theme.font_install import is_installed

    font_family = "Inter"
    if cfg.exists():
        try:
            font_family = _load_config(None).surface.fonts.family
        except Exception:
            # Best-effort display: an unparseable config must not stop
            # `status` from reporting everything else; `config validate`
            # is the command that reports the parse error itself.
            pass
    click.echo(
        f"font '{font_family}' resolves via fontconfig: "
        f"{'yes' if is_installed(font_family) else 'no'}"
    )
    click.echo(f"timer paused: {'yes' if systemd.is_paused() else 'no'}")
    # Report any QML drift so it's visible without running doctor.
    tdir = paths.templates_dir()
    if tdir.is_dir():
        drifted = []
        for name, vendor_path in extract.DEFAULT_TARGETS:
            if not vendor_path.is_file():
                continue
            rep = drift.check(name, vendor_path)
            if not rep.on_disk_matches_pristine:
                drifted.append(name)
        if drifted:
            click.echo(f"QML drift: {', '.join(drifted)}")
            click.echo(
                "  fix: trinity qml-update-templates  (or trinity apply --adopt-drift)"
            )
        else:
            click.echo("QML drift: none")


# --- config -----------------------------------------------------------


@main.group()
def config() -> None:
    """Inspect and edit the user configuration."""


@config.command("show")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
def config_show(config_path: Path | None) -> None:
    """Print the active configuration."""
    from trinity.config import load_config

    cfg = load_config(config_path)
    click.echo(cfg.model_dump_json(indent=2))


@config.command("validate")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
def config_validate(config_path: Path | None) -> None:
    """Validate the config file; exit 0 if valid, 1 otherwise."""
    from trinity.config import load_config

    try:
        load_config(config_path)
    except Exception as exc:
        click.echo(f"invalid: {exc}", err=True)
        sys.exit(1)
    click.echo("ok")


@config.command("init")
@click.option("--force", is_flag=True, help="Overwrite an existing config.")
def config_init(force: bool) -> None:
    """Write a starter config to the default location."""
    from trinity import paths
    from trinity.atomic import atomic_write_text

    target = paths.config_file()
    if target.exists() and not force:
        click.echo(f"{target} already exists; pass --force to overwrite.")
        sys.exit(2)

    text = """\
[surface]
schema_version = 1

[surface.source]
provider = "bing"

[surface.source.options]
mkt = "en-US"
resolution = "1920x1080"

[surface.fonts]
family = "Inter"
weight = "Normal"
password_character = "*"

[surface.login]
clock_format = "hh:mm"
accent_color = "#1d99f3"

[surface.lock]
on_idle_dim_seconds = 10
suppress_wake_keypress = true

[surface.behaviour]
shared_dir = "/usr/local/share/wallpapers"
user_dir = "~/.local/state/trinity"
"""
    atomic_write_text(target, text, mode=0o644)
    click.echo(f"wrote {target}")


# --- provider ---------------------------------------------------------


@main.group()
def provider() -> None:
    """Inspect available wallpaper providers."""


@provider.command("list")
def provider_list() -> None:
    """List registered providers."""
    from trinity.providers import list_providers, make_plugin_manager

    pm = make_plugin_manager()
    for info in list_providers(pm):
        marker = "[built-in]" if info.builtin else "[plugin]"
        click.echo(f"  {info.name:14s} {marker:12s} {info.description}")


@provider.command("info")
@click.argument("name")
def provider_info(name: str) -> None:
    """Show details about one provider."""
    from trinity.providers import list_providers, make_plugin_manager

    pm = make_plugin_manager()
    for info in list_providers(pm):
        if info.name == name:
            click.echo(f"name:        {info.name}")
            click.echo(f"description: {info.description}")
            click.echo(f"built-in:    {info.builtin}")
            return
    click.echo(f"no provider named {name!r}", err=True)
    sys.exit(2)


# --- qml-update-templates ---------------------------------------------


@main.command("qml-update-templates")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def qml_update_templates(yes: bool) -> None:
    """Re-extract pristine QML templates from the system."""
    from trinity.theme import extract

    if not yes and not click.confirm(
        "Overwrite stored pristine templates with current vendor QML?"
    ):
        click.echo("aborted.")
        return
    written = extract.extract()
    for path in written:
        click.echo(f"wrote {path}")


# --- doctor -----------------------------------------------------------


@main.command()
def doctor() -> None:
    """Run health checks on the install.

    Exits 0 if everything is fine, 1 if there are hard problems
    (missing config, timer disabled, QML drift). Soft warnings (font
    not found, shared dir not writable) are reported but do not change
    the exit code, because they reflect a config choice or a non-root
    run rather than a broken install.
    """
    from trinity import paths
    from trinity.theme.font_install import is_installed

    ok = True

    cfg = paths.config_file()
    if cfg.exists():
        click.echo(f"[ok]   config: {cfg}")
    else:
        click.echo(f"[warn] config missing: {cfg}")
        ok = False

    m = paths.manifest_file()
    click.echo(f"[ok]   manifest log: {m}")

    sw = paths.shared_wallpapers_dir()
    if sw.is_dir() and os.access(sw, os.W_OK):
        click.echo(f"[ok]   shared dir writable: {sw}")
    else:
        click.echo(f"[warn] shared dir not writable: {sw}")

    font_family = "Inter"
    if cfg.exists():
        try:
            from trinity.config import load_config as _load_config

            font_family = _load_config(None).surface.fonts.family
        except Exception:
            # Best-effort: doctor reports the config's presence above;
            # a parse failure falls back to checking the default font.
            pass
    if is_installed(font_family):
        click.echo(f"[ok]   font '{font_family}' resolves via fontconfig")
    else:
        click.echo(f"[warn] font '{font_family}' not found by fontconfig")

    from trinity import systemd as _systemd

    if _systemd.is_paused():
        click.echo("[info] timer is paused")
    else:
        click.echo(f"[ok]   timer enabled state: {_systemd.is_enabled()}")

    from trinity.theme import drift, extract

    tdir = paths.templates_dir()
    if tdir.is_dir():
        for name, vendor_path in extract.DEFAULT_TARGETS:
            if not vendor_path.is_file():
                continue
            rep = drift.check(name, vendor_path)
            if rep.on_disk_matches_pristine:
                click.echo(f"[ok]   '{name}': no drift")
            else:
                click.echo(
                    f"[warn] '{name}': DRIFT DETECTED "
                    "(on disk doesn't match stored pristine)"
                )
                click.echo(
                    "       fix: trinity qml-update-templates  "
                    "(or trinity apply --adopt-drift)"
                )
                ok = False
            sha = rep.pristine_sha
            click.echo(
                f"[info] stored pristine '{name}': "
                f"sha {sha[:12] if sha else 'missing'}…"
            )
    else:
        click.echo("[info] no stored pristine QML templates (run `trinity install`)")

    sys.exit(0 if ok else 1)


# --- migrate ----------------------------------------------------------


@main.command("migrate-from-shell")
@click.option("--dry-run", is_flag=True, help="Print the plan without writing.")
def migrate_from_shell(dry_run: bool) -> None:
    """Detect existing shell-based setup and generate a starter config."""
    from trinity import paths

    detected = _detect_existing_setup()
    if not detected:
        click.echo("No existing shell-based trinity setup detected.")
        return
    click.echo("Detected:")
    for key, value in detected.items():
        click.echo(f"  {key}: {value}")
    target = paths.config_file()
    if dry_run:
        click.echo(f"[dry-run] would write starter config to {target}")
        return
    if target.exists():
        click.echo(
            f"{target} already exists; refusing to overwrite.",
            err=True,
        )
        sys.exit(2)
    from trinity.config import dump_config
    from trinity.schema import (
        Behaviour,
        Config,
        Fonts,
        Lock,
        Login,
        Source,
        SourceOptions,
        Surface,
    )

    cfg = Config(
        surface=Surface(
            source=Source(
                provider="bing",
                options=SourceOptions.model_construct(
                    mkt="en-US", resolution="1920x1080"
                ),
            ),
            fonts=Fonts(),
            login=Login(),
            lock=Lock(),
            behaviour=Behaviour(
                shared_dir=detected.get("shared_dir", "/usr/local/share/wallpapers")
            ),
        )
    )
    dump_config(cfg)
    click.echo(f"wrote {target}")


# --- install ----------------------------------------------------------


@main.command()
@click.option("--yes", is_flag=True, help="Skip confirmation prompts.")
def install(yes: bool) -> None:
    """Install Inter font, create shared dir, enable systemd timer.

    Requires root for the font install and shared-dir creation steps.
    """
    from trinity import paths, systemd
    from trinity.theme import extract
    from trinity.theme.font_install import install as install_font

    click.echo("==> Extracting pristine QML templates")
    written = extract.extract()
    for p in written:
        click.echo(f"    {p}")

    click.echo("==> Installing Inter font (root recommended)")
    if not yes and not click.confirm(
        "Install Inter font to /usr/local/share/fonts? (needs sudo)"
    ):
        click.echo("    skipped (login screen will use system default font)")
    else:
        try:
            result = install_font()
            click.echo(f"    installed to {result.installed_to}")
            if result.ran_fc_cache:
                click.echo("    ran fc-cache -f")
        except OSError as exc:
            # FileNotFoundError (no bundled font) or PermissionError
            # (target dir not writable) — both are non-fatal: the login
            # screen keeps the system default font.
            click.echo(f"    font install failed: {exc}", err=True)

    click.echo("==> Creating shared wallpaper directory")
    sw = paths.shared_wallpapers_dir()
    try:
        sw.mkdir(parents=True, exist_ok=True)
        click.echo(f"    {sw}")
    except PermissionError:
        click.echo(f"    failed to create {sw}; run as root", err=True)

    click.echo("==> Installing systemd timer")
    svc, tmr = systemd.install()
    click.echo(f"    wrote {svc}")
    click.echo(f"    wrote {tmr}")
    ok, msg = systemd.enable_and_start()
    if ok:
        click.echo(f"    {msg}")
    else:
        click.echo(f"    systemd enable failed: {msg}", err=True)


@main.command()
@click.option("--yes", is_flag=True, help="Skip confirmation prompts.")
def uninstall(yes: bool) -> None:
    """Disable systemd timer and remove unit files."""
    from trinity import paths, systemd

    if not yes and not click.confirm("Disable and remove systemd user units?"):
        click.echo("aborted.")
        return
    ok, msg = systemd.disable_and_stop()
    if ok:
        click.echo(f"    {msg}")
    else:
        click.echo(f"    systemd disable failed: {msg}", err=True)

    unit_dir = paths.config_dir().parent / "systemd" / "user"
    removed = False
    for p in (unit_dir / "trinity-pull.service", unit_dir / "trinity-pull.timer"):
        if p.exists():
            p.unlink()
            removed = True
            click.echo(f"    removed {p}")
    if removed:
        # Forget the deleted units so systemd doesn't keep stale state.
        systemd.systemctl("daemon-reload")


@main.command()
def pause() -> None:
    """Temporarily stop the daily systemd timer without removing units."""
    from trinity import systemd

    ok, msg = systemd.pause()
    if ok:
        click.echo(msg)
    else:
        click.echo(f"pause failed: {msg}", err=True)
        sys.exit(1)


@main.command()
def resume() -> None:
    """Re-enable the daily systemd timer after a pause."""
    from trinity import systemd

    ok, msg = systemd.resume()
    if ok:
        click.echo(msg)
    else:
        click.echo(f"resume failed: {msg}", err=True)
        sys.exit(1)


def _detect_existing_setup() -> dict[str, str]:
    """Look for the legacy shell-based pieces on this system."""
    out: dict[str, str] = {}
    script = Path("/usr/local/bin/bing-potd.sh")
    if script.exists():
        out["shell_script"] = str(script)
    timer = Path("~/.config/systemd/user/bing-potd.timer").expanduser()
    if timer.exists():
        out["systemd_timer"] = str(timer)
    return out


def run() -> None:
    """Console-script entry point.

    Installs the user-facing excepthook (so unexpected errors render a
    clean ``error: ...`` block instead of a traceback) and dispatches to
    the Click group. Click's default ``standalone_mode=True`` catches
    exceptions itself and converts them to ``SystemExit``, bypassing
    ``sys.excepthook`` — so we run in non-standalone mode and handle
    ``CLIError`` ourselves to render the graceful error block.
    """
    _install_excepthook()
    try:
        main(standalone_mode=False)
    except CLIError as exc:
        click.echo(_format_error(str(exc), exc.hint), err=True)
        sys.exit(exc.status)
    except click.exceptions.Abort:
        click.echo("aborted.", err=True)
        sys.exit(1)
    except click.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
    # Anything else propagates and is rendered by the excepthook above.


if __name__ == "__main__":
    run()
