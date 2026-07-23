"""Click CLI entry point for trinity.

Exit-code convention
====================

trinity follows the spirit of BSD ``sysexits.h`` so users and shell
scripts can rely on stable, distinct exit codes per failure category.
The full set of constants lives in :mod:`trinity.exit_codes`; the
short version:

- ``0`` — success.
- ``1`` (``EXIT_ERROR``) — generic runtime / backend / unexpected
  error (network failure, surface write failed, manifest write
  failed, ``doctor`` reported a problem).
- ``2`` (``EXIT_USAGE``) — CLI usage error (missing argument,
  conflicting flags, etc.). Matches Click's own usage-error exit code.
- ``65`` (``EXIT_DATAERR``) — data error: the TOML config is
  malformed or fails schema validation, the manifest is unparseable.
- ``66`` (``EXIT_NOINPUT``) — missing input: a referenced provider,
  font, file, or systemd unit is not found on the system.
- ``73`` (``EXIT_CANTCREAT``) — refusing to overwrite an existing
  config or template file; the caller must re-run with ``--force``.

Every literal ``sys.exit(N)`` in this module uses one of the named
constants from :mod:`trinity.exit_codes`. ``CLIError`` carries a
``status`` field that defaults to ``EXIT_ERROR`` and is propagated to
``sys.exit`` by the top-level handler in :func:`run`.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from types import TracebackType

import click

from trinity import __version__
from trinity.exit_codes import (
    EXIT_CANTCREAT,
    EXIT_DATAERR,
    EXIT_ERROR,
    EXIT_NOINPUT,
    EXIT_USAGE,
)
from trinity.logging_setup import configure_logging, get_logger

_log = get_logger(__name__)


class CLIError(RuntimeError):
    """A user-facing error with an optional hint.

    The top-level ``main`` group catches this and prints the message and
    hint to stderr, then exits with a non-zero status. This is how
    graceful failures are signalled from anywhere in the call stack.
    """

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        status: int = EXIT_ERROR,
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


@click.group(
    invoke_without_command=True,
    epilog=(
        "Common workflows:\n"
        "\n"
        "  trinity setup\n"
        "      First-time setup (config + install + apply).\n"
        "\n"
        "  trinity apply\n"
        "      Refresh the wallpaper now.\n"
        "\n"
        "  sudo trinity apply\n"
        "      Same, but write to system SDDM dirs (theme fork, drop-in).\n"
        "\n"
        "  trinity apply --dry-run\n"
        "      Preview the plan without writing anything.\n"
        "\n"
        "  trinity apply --adopt-drift\n"
        "      Accept drifted vendor QML (after a Plasma update).\n"
        "\n"
        "  trinity apply --restart-dm\n"
        "      Restart the display manager after applying (terminates\n"
        "      the current session — opt-in only).\n"
        "\n"
        "  trinity restore\n"
        "      Undo the most recent apply.\n"
        "\n"
        "  trinity doctor\n"
        "      Run health checks on the install.\n"
        "\n"
        "  trinity status\n"
        "      Quick overview of config + manifest + drift.\n"
        "\n"
        "  trinity pause / resume\n"
        "      Temporarily stop / re-enable the hourly timer.\n"
        "\n"
        "See `trinity <command> --help` for details on each subcommand."
    ),
)
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
        sys.exit(EXIT_ERROR)

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
@click.option(
    "--restart-dm",
    is_flag=True,
    help=(
        "After applying the SDDM login wallpaper, restart the display "
        "manager so the new wallpaper takes effect immediately. "
        "TERMINATES your current Wayland session — opt-in only. "
        "Requires root (or a sudoers rule that allows the restart)."
    ),
)
@click.option(
    "--if-changed",
    "if_changed",
    is_flag=True,
    help=(
        "Skip the apply when the wallpaper is unchanged: ask the provider "
        "for a cheap change token (metadata-only request) and compare it "
        "with the state persisted by the last apply. Used by the hourly "
        "systemd timer."
    ),
)
def apply(
    dry_run: bool,
    config_path: Path | None,
    adopt_drift: bool,
    restart_dm: bool,
    if_changed: bool,
) -> None:
    """Apply the configured wallpaper to desktop, lock, and login."""
    from trinity import paths
    from trinity.config import load_config
    from trinity.manifest import Manifest
    from trinity.orchestrator import apply_to_surfaces

    if config_path is None and not paths.config_file().exists():
        raise CLIError(
            f"no config at {paths.config_file()}",
            hint="run `trinity config init` to create one",
            status=EXIT_USAGE,
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

    if cfg.surface.theme_tokens.enabled and not is_installed(cfg.surface.fonts.family):
        click.echo(
            f"Warning: font family '{cfg.surface.fonts.family}' "
            "not found by fontconfig.",
            err=True,
        )

    # Pre-flight: if we are not root and the login surface (SDDM theme)
    # is present, the login backend will fail. Warn the user clearly
    # but continue so the user-mode surfaces still get updated.
    from trinity.backends.login import login_surface_needs_root

    tokens_enabled = cfg.surface.theme_tokens.enabled
    if not dry_run and login_surface_needs_root(theme_tokens_enabled=tokens_enabled):
        click.echo(
            "Note: login (SDDM) surface requires root; "
            "that step will be skipped or fail unless you re-run with sudo.",
            err=True,
        )

    from trinity.backends.base import BackendError
    from trinity.providers import ProviderError

    manifest = Manifest()
    from trinity.config import expand_behaviour_paths
    from trinity.orchestrator import _apply_lock, _noop_lock

    expanded = expand_behaviour_paths(cfg)
    user_dir = Path(expanded.surface.behaviour.user_dir)
    lock_cm = _apply_lock(user_dir) if not dry_run else _noop_lock()
    with lock_cm:
        try:
            plan = apply_to_surfaces(
                cfg,
                manifest=manifest,
                dry_run=dry_run,
                adopt_drift=adopt_drift,
                restart_dm=restart_dm,
                if_changed=if_changed,
            )
        except (ProviderError, BackendError) as exc:
            raise CLIError(str(exc)) from exc
    for line in plan:
        click.echo(line)
    # The orchestrator already emits a precise "restart <dm>" or
    # "log out fully" hint when the login surface was actually updated,
    # so we only add a generic note when nothing login-related was said.
    # An --if-changed run that skipped the apply ("unchanged") wrote
    # nothing, so the note would be noise in the hourly timer's journal.
    applied_something = any(line.startswith("wrote ") for line in plan)
    if (
        not dry_run
        and applied_something
        and not any("login wallpaper updated" in line for line in plan)
        and not any("login" in line.lower() for line in plan)
    ):
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
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview the restore operations without writing anything.",
)
def restore(to_timestamp: str | None, yes: bool, dry_run: bool) -> None:
    """Revert every recorded change."""
    from trinity.manifest import Manifest
    from trinity.manifest import restore as _restore

    m = Manifest()
    if not m.path.exists():
        click.echo("manifest log is empty; nothing to restore.")
        return
    entries = m.iter_entries()
    if to_timestamp is not None:
        to_restore = [e for e in entries if e.ts > to_timestamp]
    else:
        to_restore = list(entries)
    to_restore.reverse()

    if dry_run:
        click.echo(f"Would restore {len(to_restore)} file(s):")
        for entry in to_restore:
            if entry.op == "write":
                if entry.prev_bytes_path and Path(entry.prev_bytes_path).exists():
                    click.echo(
                        f"  restore {entry.path} "
                        f"(from snapshot {entry.prev_bytes_path})"
                    )
                elif entry.prev_sha256 is None:
                    click.echo(f"  delete {entry.path} (was newly created)")
                else:
                    click.echo(
                        f"  CANNOT restore {entry.path} "
                        f"(missing snapshot {entry.prev_bytes_path})"
                    )
            elif entry.op == "delete":
                click.echo(f"  re-delete {entry.path}")
        return

    if not yes and not click.confirm(f"Restore {len(to_restore)} recorded change(s)?"):
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

    # Report theme tokens status and any QML drift.  When theme_tokens
    # is disabled, skip the per-file drift loop entirely — the
    # per-file Pydantic Field has been migrated to opt-in, and walking
    # the vendor files would just spam "QML drift: none" for users who
    # don't care.
    theme_tokens_enabled = True
    if cfg.exists():
        try:
            theme_tokens_enabled = _load_config(None).surface.theme_tokens.enabled
        except Exception:
            pass
    click.echo(f"theme tokens: {'enabled' if theme_tokens_enabled else 'disabled'}")
    if not theme_tokens_enabled:
        return

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
        sys.exit(EXIT_DATAERR)
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
        sys.exit(EXIT_CANTCREAT)

    text = """\
[surface]
schema_version = 1

[surface.source]
provider = "bing"

[surface.source.options]
mkt = "en-US"
resolution = "1920x1080"

[surface.theme_tokens]
# Opt-in: enable QML patching for login/lock screen font and theme tokens.
# When disabled (the default), apply skips all QML patching — the simple
# wallpaper-sync use case. Set enabled = true to use the font/lock/login
# token sections below.
enabled = false

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


# --- setup ------------------------------------------------------------


@main.command()
@click.option("--yes", is_flag=True, help="Skip all confirmation prompts.")
@click.pass_context
def setup(ctx: click.Context, yes: bool) -> None:
    """First-time setup: config init → install → apply --dry-run → apply.

    Chains the three commands a new user needs in order.  Skips
    ``config init`` if a config already exists.  The dry-run output is
    always shown; the final ``apply`` requires confirmation unless
    ``--yes`` is passed.
    """
    from trinity import paths

    if paths.config_file().exists():
        click.echo(f"config exists at {paths.config_file()} — skipping config init")
    else:
        click.echo("==> Step 1/4: generating starter config")
        ctx.invoke(config_init, force=False)

    click.echo()
    click.echo("==> Step 2/4: installing (font, shared dir, systemd timer)")
    click.echo("    (may require sudo for system-level changes)")
    try:
        ctx.invoke(install, yes=yes)
    except SystemExit as exc:
        if exc.code not in (None, 0):
            click.echo(
                f"install exited with code {exc.code}; continuing to dry-run "
                "anyway (the timer may simply be unprivileged).",
                err=True,
            )

    click.echo()
    click.echo("==> Step 3/4: dry-run apply (preview without writing)")
    ctx.invoke(apply, dry_run=True, config_path=None, adopt_drift=False)

    if not yes and not click.confirm(
        "Dry-run looks good — apply the wallpaper now?", default=True
    ):
        click.echo("aborted. Run `trinity apply` when ready.")
        return

    click.echo()
    click.echo("==> Step 4/4: applying wallpaper")
    ctx.invoke(apply, dry_run=False, config_path=None, adopt_drift=False)


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
    """Show details about one provider, including option schema."""
    from trinity.providers import (
        get_provider_options_schema,
        list_providers,
        make_plugin_manager,
    )

    pm = make_plugin_manager()
    for info in list_providers(pm):
        if info.name == name:
            click.echo(f"name:        {info.name}")
            click.echo(f"description: {info.description}")
            click.echo(f"built-in:    {info.builtin}")
            schema_cls = get_provider_options_schema(pm, name)
            if schema_cls is not None:
                click.echo("options:")
                for field_name, field in schema_cls.model_fields.items():
                    type_name = getattr(
                        field.annotation, "__name__", str(field.annotation)
                    )
                    default = (
                        field.default if field.default is not None else "(required)"
                    )
                    desc = field.description or ""
                    click.echo(
                        f"  {field_name:20s} {type_name:12s} "
                        f"default={default!s:12s} {desc}"
                    )
            else:
                click.echo("options:     (no schema declared — not validated)")
            return
    click.echo(f"no provider named {name!r}", err=True)
    sys.exit(EXIT_NOINPUT)


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
    theme_tokens_enabled = True
    if cfg.exists():
        try:
            from trinity.config import load_config as _load_config

            loaded = _load_config(None)
            font_family = loaded.surface.fonts.family
            theme_tokens_enabled = loaded.surface.theme_tokens.enabled
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

    click.echo(
        f"[info] theme tokens: "
        f"{'enabled' if theme_tokens_enabled else 'disabled (QML checks skipped)'}"
    )
    if not theme_tokens_enabled:
        sys.exit(EXIT_ERROR if not ok else 0)

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

    sys.exit(0 if ok else EXIT_ERROR)


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
        sys.exit(EXIT_CANTCREAT)
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


# --- cycle ------------------------------------------------------------


@main.command()
@click.option(
    "--offset",
    type=click.IntRange(0, 6),
    default=None,
    help=(
        "Day offset (0 = today, 1 = yesterday, … 6 = 6 days ago). "
        "If omitted, increments the current offset by 1 (mod 7)."
    ),
)
@click.option("--dry-run", is_flag=True, help="Preview the plan without writing.")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
def cycle(offset: int | None, dry_run: bool, config_path: Path | None) -> None:
    """Cycle retrospectively through the past 7 days of wallpapers.

    The base config.toml is never mutated; the active temporal offset
    is persisted in refresh_state.json.  The hourly ``--if-changed``
    timer respects the persisted offset so a manual cycle is not
    clobbered until the upstream master image changes.
    """
    from trinity import paths
    from trinity.config import load_config
    from trinity.manifest import Manifest
    from trinity.orchestrator import apply_to_surfaces
    from trinity.refresh_state import STATE_FILENAME, load, now_iso, save

    if config_path is None and not paths.config_file().exists():
        raise CLIError(
            f"no config at {paths.config_file()}",
            hint="run `trinity config init` to create one",
            status=EXIT_USAGE,
        )

    from pydantic import ValidationError

    try:
        cfg = load_config(config_path)
    except (ValidationError, ValueError, OSError) as exc:
        raise CLIError(
            f"invalid config {config_path or paths.config_file()}: {exc}",
            hint="run `trinity config validate` after fixing the file",
        ) from exc

    from trinity.providers import get_provider_options_schema, make_plugin_manager
    pm = make_plugin_manager()
    schema = get_provider_options_schema(pm, cfg.surface.source.provider)
    if schema is None or "index" not in schema.model_fields:
        p_name = cfg.surface.source.provider
        raise CLIError(
            f"provider '{p_name}' does not support temporal cycling",
            status=EXIT_USAGE,
        )

    # Determine the target offset.
    user_dir = Path(cfg.surface.behaviour.user_dir).expanduser()
    state_file = user_dir / STATE_FILENAME
    prior_state = load(state_file)
    current_offset = prior_state.temporal_offset if prior_state else 0
    if offset is not None:
        target_offset = offset
    else:
        target_offset = (current_offset + 1) % 7

    click.echo(f"cycle: offset {target_offset} (was {current_offset})")

    from trinity.backends.base import BackendError
    from trinity.providers import ProviderError

    manifest = Manifest()
    try:
        plan = apply_to_surfaces(
            cfg,
            manifest=manifest,
            dry_run=dry_run,
            temporal_offset=target_offset,
        )
    except (ProviderError, BackendError) as exc:
        raise CLIError(str(exc)) from exc
    for line in plan:
        click.echo(line)

    # Persist the new offset.
    if not dry_run and any(line.startswith("wrote ") for line in plan):
        from trinity.refresh_state import RefreshState

        # Re-read to get the latest state (apply may have saved its own)
        new_state = load(state_file)
        if new_state is not None:
            save(
                state_file,
                RefreshState(
                    fingerprint=new_state.fingerprint,
                    probe_token=new_state.probe_token,
                    image_sha256=new_state.image_sha256,
                    wallpaper_path=new_state.wallpaper_path,
                    applied_at=now_iso(),
                    temporal_offset=target_offset,
                ),
            )


# --- install ----------------------------------------------------------


@main.command()
@click.option("--yes", is_flag=True, help="Skip confirmation prompts.")
@click.option(
    "--wake-network",
    is_flag=True,
    help=(
        "Also install a NetworkManager dispatcher script that runs "
        "trinity apply --if-changed when Wi-Fi reconnects. Requires root."
    ),
)
def install(yes: bool, wake_network: bool) -> None:
    """Install Inter font, create shared dir, enable systemd timer.

    Requires root for the font install and shared-dir creation steps.
    """
    from trinity import paths, systemd
    from trinity.config import load_config as _load_config
    from trinity.theme import extract
    from trinity.theme.font_install import install as install_font

    # theme_tokens is opt-in; only extract QML templates if enabled.
    tokens_enabled = True
    if paths.config_file().exists():
        try:
            tokens_enabled = _load_config(None).surface.theme_tokens.enabled
        except Exception:
            # If config is broken, proceed with extraction (don't block
            # install on a broken config — the user can run `trinity
            # config validate` separately).
            pass
    elif not yes:
        # No config file at all: refuse with a usage error and point the
        # user at `setup`.  Skipped under --yes so a non-interactive
        # install pipeline (e.g. dotfiles) can still proceed; the
        # default theme_tokens is `true` so the extraction below is the
        # same path either way.
        click.echo(
            f"no config found at {paths.config_file()}; "
            "run `trinity setup` (or `trinity config init`) first.",
            err=True,
        )
        sys.exit(EXIT_USAGE)

    if tokens_enabled:
        click.echo("==> Extracting pristine QML templates")
        written = extract.extract()
        for p in written:
            click.echo(f"    {p}")
    else:
        click.echo(
            "==> Extracting pristine QML templates (skipped: theme_tokens disabled)"
        )

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
    svc, tmr = systemd.install(wake_system=wake_network)
    click.echo(f"    wrote {svc}")
    click.echo(f"    wrote {tmr}")
    ok, msg = systemd.enable_and_start()
    if ok:
        click.echo(f"    {msg}")
    else:
        click.echo(f"    systemd enable failed: {msg}", err=True)

    if wake_network:
        click.echo("==> Installing NetworkManager dispatcher (requires root)")
        if os.geteuid() != 0:
            click.echo(
                "    --wake-network requires root; re-run with sudo",
                err=True,
            )
            sys.exit(EXIT_ERROR)
        import pwd

        from trinity.systemd.network_dispatcher import (
            install_network_dispatcher_script,
        )

        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user:
            target_user = sudo_user
        else:
            target_user = pwd.getpwuid(os.getuid()).pw_name

        # Validate RTC wakealarm availability
        rtc_path = Path("/sys/class/rtc/rtc0/wakealarm")
        if not rtc_path.exists():
            click.echo(
                "    warning: /sys/class/rtc/rtc0/wakealarm not found; "
                "RTC wake will not work on this hardware",
                err=True,
            )

        try:
            disp_path = install_network_dispatcher_script(target_user)
            click.echo(f"    wrote {disp_path}")
        except OSError as exc:
            click.echo(f"    dispatcher install failed: {exc}", err=True)


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
        sys.exit(EXIT_ERROR)


@main.command()
def resume() -> None:
    """Re-enable the daily systemd timer after a pause."""
    from trinity import systemd

    ok, msg = systemd.resume()
    if ok:
        click.echo(msg)
    else:
        click.echo(f"resume failed: {msg}", err=True)
        sys.exit(EXIT_ERROR)


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
    clean ``error: ...`` block instead of a traceback) and a SIGTERM
    handler (so systemd's ``systemctl stop`` unwinds ``finally`` blocks
    and context managers instead of killing the process mid-write).

    Click's default ``standalone_mode=True`` catches exceptions itself
    and converts them to ``SystemExit``, bypassing ``sys.excepthook`` —
    so we run in non-standalone mode and handle ``CLIError`` ourselves
    to render the graceful error block.
    """
    _install_excepthook()
    _install_sigterm_handler()
    try:
        main(standalone_mode=False)
    except CLIError as exc:
        click.echo(_format_error(str(exc), exc.hint), err=True)
        sys.exit(exc.status)
    except click.exceptions.Abort:
        click.echo("aborted.", err=True)
        sys.exit(EXIT_ERROR)
    except click.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
    # Anything else propagates and is rendered by the excepthook above.


def _install_sigterm_handler() -> None:
    """Install a SIGTERM handler that raises ``SystemExit(143)``.

    Under systemd, ``SIGTERM`` is sent on ``systemctl --user stop`` (or
    when ``TimeoutStopSec`` expires).  The default disposition kills the
    process immediately (exit 143), which can interrupt a write between
    the atomic file replace and the manifest append — leaving an untracked
    write that breaks the undo guarantee.

    By converting SIGTERM to ``SystemExit(143)`` we let Python's normal
    unwinding run: ``finally`` blocks fire, context managers close files,
    and the manifest stays consistent.  The exit code 143 (128 + 15)
    matches what systemd expects for a SIGTERM-terminated process.

    Only SIGTERM is handled here — SIGINT already works via
    ``KeyboardInterrupt`` in the excepthook.  The handler is installed
    only in ``run()`` (the console-script entry point), not on library
    import, so importing ``trinity.cli`` in tests has no side effects.
    """
    import signal

    def _on_sigterm(signum: int, frame: object) -> None:
        _log.warning("sigterm_received", signal=signum)
        raise SystemExit(143)

    # Only install if we're in the main thread (signal handlers can only
    # be installed from the main thread; tests that import cli.run don't
    # always run in the main thread).
    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        # Not in the main thread — skip (tests, embedded contexts).
        pass


if __name__ == "__main__":
    run()
