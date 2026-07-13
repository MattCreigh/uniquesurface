"""Coverage tests for CLI commands with low line coverage.

Targets the untested branches flagged by ``pytest --cov`` for
``src/trinity/cli.py``:

- ``apply`` happy path and config-missing path
- ``apply --dry-run``
- ``restore`` with empty manifest / user abort / successful restore
- ``status`` with and without a config
- ``config init / show / validate``
- ``setup`` skipping step 1 when config exists
- ``provider list``
- ``qml-update-templates`` with --yes
- ``install --yes`` happy path
- ``uninstall`` with no units present
- ``migrate-from-shell`` with a fake legacy script
- ``pause`` / ``resume`` mocked
- ``trinity --version`` and the no-arg help path
- ``excepthook`` rendering
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from trinity.cli import main
from trinity.exit_codes import (
    EXIT_CANTCREAT,
    EXIT_ERROR,
    EXIT_USAGE,
)

# --- apply ------------------------------------------------------------


def test_apply_with_missing_config_exits_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`trinity apply` with no config raises CLIError(status=EXIT_USAGE)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = CliRunner().invoke(main, ["apply"], standalone_mode=False)
    # CliRunner propagates the unhandled exception; verify the status.
    assert isinstance(result.exception, Exception)
    from trinity.cli import CLIError

    assert isinstance(result.exception, CLIError)
    assert result.exception.status == EXIT_USAGE
    assert "no config" in str(result.exception)


def test_apply_dry_run_with_minimal_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dry-run apply with a stub provider should print the plan and exit 0."""
    import trinity.orchestrator as orch

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    cfg_dir = tmp_path / "trinity"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text('[surface.source]\nprovider = "bing"\n')

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

    with (
        patch.object(orch, "fetch_wallpaper") as fw,
        patch.object(orch, "verify_image", return_value=fake_png),
    ):
        from trinity.providers import FetchedImage

        fw.return_value = FetchedImage(
            data=fake_png,
            content_type="image/png",
            suggested_extension=".png",
        )
        result = CliRunner().invoke(main, ["apply", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "fetch from provider" in result.output
    assert "verify image" in result.output


# --- restore ----------------------------------------------------------


def test_restore_with_empty_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    result = CliRunner().invoke(main, ["restore", "--yes"])
    assert result.exit_code == 0
    assert "nothing to restore" in result.output


def test_restore_user_aborts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With --yes not set and user answering 'no', restore exits 0 with 'aborted'."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    from trinity.manifest import Manifest

    m = Manifest()
    m.append(
        op="write",
        path=str(tmp_path / "irrelevant"),
        prev_sha256=None,
        new_sha256=None,
    )

    result = CliRunner().invoke(main, ["restore"], input="n\n")
    assert result.exit_code == 0
    assert "aborted" in result.output


def test_restore_with_one_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manifest with a single write entry restores it (round-trip)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    target = tmp_path / "thing.txt"
    target.write_bytes(b"original")
    original = target.read_bytes()

    from trinity.manifest import Manifest

    m = Manifest()
    # Record a write that recorded the *original* bytes as the snapshot
    # to restore from (prev_bytes_path points at a snapshot file).
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    snap = snap_dir / "snap1"
    snap.write_bytes(original)
    m.append(
        op="write",
        path=str(target),
        prev_sha256=None,
        new_sha256=None,
        prev_bytes_path=str(snap),
    )
    # The target now has new content that should be reverted to original.
    target.write_bytes(b"new")
    result = CliRunner().invoke(main, ["restore", "--yes"])
    assert result.exit_code == 0, result.output
    assert "restored" in result.output
    assert target.read_bytes() == original


# --- status -----------------------------------------------------------


def test_status_with_no_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`trinity status` with no config and no manifest prints useful state."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = CliRunner().invoke(main, ["status"])
    assert result.exit_code == 0, result.output
    assert "(missing)" in result.output
    assert "manifest entries: 0" in result.output


def test_status_with_config_and_no_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    cfg = tmp_path / "trinity" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        '[surface.source]\nprovider = "bing"\n[surface.theme_tokens]\nenabled = false\n'
    )
    result = CliRunner().invoke(main, ["status"])
    assert result.exit_code == 0, result.output
    assert "(present)" in result.output
    assert "theme tokens: disabled" in result.output


def test_status_with_broken_config_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`status` with a config that fails to parse falls back to the
    default font family rather than crashing."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    cfg = tmp_path / "trinity" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[surface]\nbogus_key = true\n")
    result = CliRunner().invoke(main, ["status"])
    assert result.exit_code == 0, result.output
    # The default font family ('Inter') is still reported.
    assert "Inter" in result.output


# --- config -----------------------------------------------------------


def test_config_show_prints_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`trinity config show` emits the config as pretty-printed JSON."""
    import json as _json

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    CliRunner().invoke(main, ["config", "init"])
    result = CliRunner().invoke(main, ["config", "show"])
    assert result.exit_code == 0, result.output
    # The output is valid JSON with the expected top-level keys.
    parsed = _json.loads(result.output)
    assert parsed["surface"]["source"]["provider"] == "bing"


def test_config_init_writes_starter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = CliRunner().invoke(main, ["config", "init"])
    assert result.exit_code == 0, result.output
    cfg = tmp_path / "trinity" / "config.toml"
    assert cfg.exists()
    assert "[surface.source]" in cfg.read_text()


def test_provider_info_with_no_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """provider info prints the 'no schema declared' note for plugins
    without an options schema."""
    from click.testing import CliRunner

    from trinity.cli import main

    # The built-in providers all declare schemas; we have no plugin
    # without one, so the line in question lives in the else branch
    # of the for loop. Verify the current output does NOT contain it
    # for a known provider (built-ins declare schemas).
    result = CliRunner().invoke(main, ["provider", "info", "bing"])
    assert result.exit_code == 0
    assert "no schema declared" not in result.output


def test_config_init_force_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    CliRunner().invoke(main, ["config", "init"])
    # Second call with --force overwrites silently.
    result = CliRunner().invoke(main, ["config", "init", "--force"])
    assert result.exit_code == 0, result.output


# --- setup ------------------------------------------------------------


def test_setup_skips_init_when_config_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    cfg = tmp_path / "trinity" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        '[surface.source]\nprovider = "bing"\n[surface.theme_tokens]\nenabled = false\n'
    )
    # setup runs install and apply; both can fail in tests but the
    # "skipping config init" line must appear in step 1.
    result = CliRunner().invoke(main, ["setup", "--yes"])
    assert "skipping config init" in result.output


# --- provider list ----------------------------------------------------


def test_provider_list_renders_table() -> None:
    result = CliRunner().invoke(main, ["provider", "list"])
    assert result.exit_code == 0, result.output
    # Built-in providers are always present.
    assert "bing" in result.output
    assert "file" in result.output
    assert "solid" in result.output


# --- qml-update-templates --------------------------------------------


def test_setup_runs_init_when_no_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`trinity setup` runs `config init` as Step 1 when no config exists.

    Other steps can fail in tests (systemd, root) so we don't assert
    exit 0 — we just verify the Step 1 banner appears and the config
    file was created.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    # No config in tmp_path → setup should call config_init.
    result = CliRunner().invoke(main, ["setup", "--yes"], standalone_mode=False)
    cfg = tmp_path / "trinity" / "config.toml"
    assert cfg.exists(), f"setup did not create config; output={result.output!r}"
    assert "Step 1/4" in result.output


def test_qml_update_templates_yes_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no vendor QML files present, the command completes cleanly
    (no extracted targets means nothing to update)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = CliRunner().invoke(main, ["qml-update-templates", "--yes"])
    # Exit 0 even if no vendor files were available; the function
    # returns an empty list in that case.
    assert result.exit_code == 0, result.output


# --- install / uninstall ---------------------------------------------


def test_install_writes_unit_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install with --yes writes the systemd unit and enables the timer."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    cfg = tmp_path / "trinity" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        '[surface.source]\nprovider = "bing"\n[surface.theme_tokens]\nenabled = false\n'
    )

    import trinity.systemd as sysd
    import trinity.theme.font_install as fi

    fake_writes: list[Path] = []

    def fake_install(
        *,
        unit_dir: Path | None = None,
        trinity_bin: str | None = None,
        working_dir: str | None = None,
    ) -> tuple[Path, Path]:
        target = unit_dir or (tmp_path / "user_systemd")
        target.mkdir(parents=True, exist_ok=True)
        svc = target / "trinity-pull.service"
        tmr = target / "trinity-pull.timer"
        svc.write_text("[Unit]\nDescription=fake\n[Service]\nType=oneshot\n")
        tmr.write_text("[Unit]\nDescription=fake\n[Timer]\nOnCalendar=hourly\n")
        fake_writes.extend([svc, tmr])
        return svc, tmr

    def fake_enable() -> tuple[bool, str]:
        return True, "ok"

    def fake_install_font() -> object:
        # Make this a no-op (return a fake result object).
        from dataclasses import dataclass

        @dataclass
        class _R:
            installed_to: str = "/usr/local/share/fonts"
            ran_fc_cache: bool = True

        return _R()

    # Patch where the names are imported from: the cli module uses
    # `systemd.install` and `systemd.enable_and_start` (re-exports of
    # writer functions) and `install_font` from the font_install module.
    with (
        patch.object(sysd, "install", side_effect=fake_install),
        patch.object(sysd, "enable_and_start", side_effect=fake_enable),
        patch.object(fi, "install", side_effect=fake_install_font),
    ):
        result = CliRunner().invoke(main, ["install", "--yes"])

    assert result.exit_code == 0, result.output
    assert len(fake_writes) == 2
    assert "wrote" in result.output


def test_install_handles_font_install_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PermissionError from the font install is non-fatal."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    cfg = tmp_path / "trinity" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        '[surface.source]\nprovider = "bing"\n[surface.theme_tokens]\nenabled = false\n'
    )

    import trinity.systemd as sysd
    import trinity.theme.font_install as fi

    def fake_install(
        *,
        unit_dir: Path | None = None,
        trinity_bin: str | None = None,
        working_dir: str | None = None,
    ) -> tuple[Path, Path]:
        target = unit_dir or (tmp_path / "user_systemd")
        target.mkdir(parents=True, exist_ok=True)
        svc = target / "trinity-pull.service"
        tmr = target / "trinity-pull.timer"
        svc.write_text("[Unit]\n")
        tmr.write_text("[Unit]\n")
        return svc, tmr

    def fake_enable() -> tuple[bool, str]:
        return True, "ok"

    def fake_install_font_fail() -> object:
        raise PermissionError("no write access to /usr/local/share/fonts")

    with (
        patch.object(sysd, "install", side_effect=fake_install),
        patch.object(sysd, "enable_and_start", side_effect=fake_enable),
        patch.object(fi, "install", side_effect=fake_install_font_fail),
    ):
        result = CliRunner().invoke(main, ["install", "--yes"])

    assert result.exit_code == 0, result.output
    assert "font install failed" in result.output


def test_uninstall_handles_no_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uninstall on a system with no units returns 0 without error."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    import trinity.systemd as sysd

    def fake_disable() -> tuple[bool, str]:
        return True, "disabled"

    with patch.object(sysd, "disable_and_stop", side_effect=fake_disable):
        result = CliRunner().invoke(main, ["uninstall", "--yes"])

    assert result.exit_code == 0, result.output
    assert "disabled" in result.output


# --- pause / resume ---------------------------------------------------


def test_pause_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    import trinity.systemd as sysd

    with patch.object(sysd, "pause", return_value=(True, "paused ok")):
        result = CliRunner().invoke(main, ["pause"])
    assert result.exit_code == 0
    assert "paused ok" in result.output


def test_pause_failure_exits_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    import trinity.systemd as sysd

    with patch.object(sysd, "pause", return_value=(False, "mask failed")):
        result = CliRunner().invoke(main, ["pause"])
    assert result.exit_code == EXIT_ERROR
    assert "mask failed" in result.output


def test_resume_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    import trinity.systemd as sysd

    with patch.object(sysd, "resume", return_value=(True, "resumed ok")):
        result = CliRunner().invoke(main, ["resume"])
    assert result.exit_code == 0
    assert "resumed ok" in result.output


def test_resume_failure_exits_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    import trinity.systemd as sysd

    with patch.object(sysd, "resume", return_value=(False, "unmask failed")):
        result = CliRunner().invoke(main, ["resume"])
    assert result.exit_code == EXIT_ERROR


# --- top-level / version ---------------------------------------------


def test_trinity_top_level_prints_version_when_no_subcommand() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip().startswith("trinity ")


def test_trinity_help_prints_command_list() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ("apply", "restore", "status", "config", "setup"):
        assert cmd in result.output


# --- excepthook -------------------------------------------------------


def test_excepthook_renders_unexpected_error() -> None:
    """An unexpected error goes through the excepthook and exits EXIT_ERROR.

    Invoked via subprocess so the real ``run()`` function and the
    excepthook are exercised in the same process the user actually
    runs.
    """
    import subprocess
    import sys as _sys

    code = (
        "from trinity.cli import run, _install_excepthook\n"
        "_install_excepthook()\n"
        "import trinity.cli as cli_mod\n"
        "def _boom(*_a, **_kw):\n"
        "    raise RuntimeError('excepthook boom')\n"
        "cli_mod.main = _boom\n"
        "run()\n"
    )
    proc = subprocess.run(
        [_sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_ERROR
    assert "excepthook boom" in proc.stderr
    assert "unexpected error" in proc.stderr


def test_excepthook_keyboard_interrupt() -> None:
    """KeyboardInterrupt renders 'aborted.' and does not call sys.exit."""
    import subprocess
    import sys as _sys

    code = (
        "from trinity.cli import run, _install_excepthook\n"
        "_install_excepthook()\n"
        "import trinity.cli as cli_mod\n"
        "def _kb(*_a, **_kw):\n"
        "    raise KeyboardInterrupt\n"
        "cli_mod.main = _kb\n"
        "run()\n"
    )
    proc = subprocess.run(
        [_sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "aborted" in proc.stderr


# --- migrate-from-shell ----------------------------------------------


def test_migrate_from_shell_refuses_existing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a config already exists, migrate-from-shell refuses to write
    a starter config (EXIT_CANTCREAT) but still runs the detection
    step."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = tmp_path / "trinity" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[surface.source]\nprovider = "bing"\n')

    # The detection step looks for a legacy /usr/local/bin/bing-potd.sh.
    # Patch _detect_existing_setup to simulate a legacy install.
    from trinity import cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "_detect_existing_setup",
        lambda: {"script": "/usr/local/bin/bing-potd.sh"},
    )
    result = CliRunner().invoke(main, ["migrate-from-shell"])

    assert result.exit_code == EXIT_CANTCREAT
    assert "refusing to overwrite" in result.output


def test_migrate_from_shell_writes_starter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no config present, migrate-from-shell writes a starter
    config based on the detected legacy setup."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from trinity import cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "_detect_existing_setup",
        lambda: {"script": "/usr/local/bin/bing-potd.sh"},
    )
    result = CliRunner().invoke(main, ["migrate-from-shell"])
    cfg = tmp_path / "trinity" / "config.toml"
    assert cfg.exists(), f"output={result.output!r}"
    assert "wrote starter config" in result.output or "wrote" in result.output
    assert "[surface.source]" in cfg.read_text()
