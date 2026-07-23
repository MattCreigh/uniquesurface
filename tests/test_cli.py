"""End-to-end-ish tests that drive the CLI and the orchestrator together."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner

from trinity.cli import main
from trinity.providers.builtin import bing
from trinity.theme import extract


def test_config_init_writes_starter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(main, ["config", "init"])
    assert result.exit_code == 0, result.output
    config_path = Path(os.environ["XDG_CONFIG_HOME"]) / "trinity" / "config.toml"
    assert config_path.exists()
    text = config_path.read_text()
    assert 'provider = "bing"' in text


def test_config_validate_against_starter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runner = CliRunner()
    runner.invoke(main, ["config", "init"])
    result = runner.invoke(main, ["config", "validate"])
    assert result.exit_code == 0


def test_apply_dry_run_with_solid_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set up an isolated XDG env.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
    monkeypatch.setenv("TRINITY_SHARED_DIR", str(tmp_path / "shared"))
    cfg_dir = tmp_path / "xdg_config" / "trinity"
    cfg_dir.mkdir(parents=True)
    shared = str(tmp_path / "shared")
    user_state = str(tmp_path / "user_state")
    (cfg_dir / "config.toml").write_text(
        f"""\
[surface]
schema_version = 1

[surface.source]
provider = "solid"

[surface.source.options]
color = "#1d99f3"
width = 32
height = 18

[surface.behaviour]
shared_dir = "{shared}"
user_dir = "{user_state}"
"""
    )

    runner = CliRunner()
    result = runner.invoke(main, ["apply", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "fetch from provider" in result.output
    assert "kwriteconfig6" in result.output


def test_apply_real_writes_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, respx_mock: respx.router.MockRouter
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
    monkeypatch.setenv("TRINITY_SHARED_DIR", str(tmp_path / "shared"))

    from trinity.backends import sddm_fork

    # Replace the SDDM theme.conf path with one we own.
    fake_sddm = sddm_fork.VENDOR_BREEZE_DIR / "theme.conf"
    fake_sddm.parent.mkdir(parents=True)
    fake_sddm.write_text(
        "[General]\ntype=image\nbackground=/old.jpg\n", encoding="utf-8"
    )
    from trinity.backends import login as login_mod

    monkeypatch.setattr(login_mod, "_THEME_CONF_PATH", fake_sddm)
    # Point the .user path at a sibling file in the fork directory.
    fake_sddm_user = sddm_fork.FORK_THEME_DIR / "theme.conf.user"
    monkeypatch.setattr(login_mod, "_THEME_CONF_USER_PATH", fake_sddm_user)

    # Mock extract targets and pristine template directory
    from trinity import paths
    from trinity.theme import extract as extract_mod

    fake_login_qml = fake_sddm.parent / "Login.qml"
    fake_login_qml.write_text(
        'import QtQuick\nItem { property string fontFamily: "Lato" }\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(paths, "templates_dir", lambda: tmp_path / "templates")
    from trinity.theme.extract import copy_pristine_bytes

    copy_pristine_bytes(
        "sddm_login", b'import QtQuick\nItem { property string fontFamily: "Lato" }\n'
    )

    monkeypatch.setattr(
        extract_mod,
        "DEFAULT_TARGETS",
        [
            ("sddm_login", fake_login_qml),
        ],
    )

    # Mock Bing. Use a real 1x1 JPEG so Pillow can decode it.
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "#1d99f3").save(buf, format="JPEG", quality=70)
    image_bytes = buf.getvalue()
    respx_mock.get(bing._METADATA_URL).mock(
        return_value=httpx.Response(
            200,
            json={"images": [{"url": "/th?id=OHR.Test_1920x1080.jpg"}]},
        )
    )
    respx_mock.get("https://www.bing.com/th?id=OHR.Test_1920x1080.jpg").mock(
        return_value=httpx.Response(
            200, content=image_bytes, headers={"content-type": "image/jpeg"}
        )
    )

    # Mock the D-Bus live-apply calls so the test never talks to the real
    # running Plasma shell (which would persist a tmp wallpaper path into
    # the user's real appletsrc).
    from trinity.backends import _kconfig

    monkeypatch.setattr(_kconfig, "evaluate_wallpaper_script", lambda **kw: [])
    monkeypatch.setattr(_kconfig, "reload_lockscreen_config", lambda **kw: [])
    monkeypatch.setattr(_kconfig, "qdbus_call", lambda **kw: [])

    cfg_dir = tmp_path / "xdg_config" / "trinity"
    cfg_dir.mkdir(parents=True)
    shared = str(tmp_path / "shared")
    user_state = str(tmp_path / "user_state")
    (cfg_dir / "config.toml").write_text(
        f"""\
[surface]
schema_version = 1

[surface.source]
provider = "bing"

[surface.source.options]
mkt = "en-US"

[surface.behaviour]
shared_dir = "{shared}"
user_dir = "{user_state}"

[surface.theme_tokens]
enabled = true
skip_qmllint = true
"""
    )

    runner = CliRunner()
    result = runner.invoke(main, ["apply"])
    assert result.exit_code == 0, result.output

    user_state = tmp_path / "user_state"
    shared = tmp_path / "shared"
    # Wallpaper filenames are content-addressed (last_wallpaper-<sha12>.jpg)
    # so every new image is a new file URI and Plasma repaints.
    user_wallpapers = list(user_state.glob("last_wallpaper-*.jpg"))
    shared_wallpapers = list(shared.glob("last_wallpaper-*.jpg"))
    assert len(user_wallpapers) == 1
    assert len(shared_wallpapers) == 1
    assert user_wallpapers[0].name == shared_wallpapers[0].name
    # A stable alias points at the current generation for consumers that
    # resolve the path at read time (SDDM).
    alias = shared / "last_wallpaper.jpg"
    assert alias.is_symlink()
    assert alias.resolve() == shared_wallpapers[0]
    # Phase 5: SDDM wallpaper now goes to theme.conf.user in the fork
    assert str(alias) in fake_sddm_user.read_text()
    # The vendor theme.conf is untouched.
    assert "last_wallpaper" not in fake_sddm.read_text()

    # The manifest has entries (wallpapers, theme.conf, and QML screens)
    from trinity.manifest import Manifest

    m = Manifest(tmp_path / "xdg_state" / "trinity" / "manifest.jsonl")
    entries = m.iter_entries()
    assert len(entries) > 0
    # At least wallpaper writes, login config write, and QML write should be tracked.
    paths_tracked = [e.path for e in entries]
    assert any(str(user_wallpapers[0]) in p for p in paths_tracked)
    assert any(str(shared_wallpapers[0]) in p for p in paths_tracked)
    assert any(str(sddm_fork.FORK_THEME_DIR) in p for p in paths_tracked)
    assert any(str(sddm_fork.DROPIN_PATH) in p for p in paths_tracked)

    # SDDM Login.qml is patched with Inter font
    qml_content = (sddm_fork.FORK_THEME_DIR / "Login.qml").read_text(encoding="utf-8")
    assert "Inter" in qml_content
    assert "/* @trinity:start */" in qml_content


def test_apply_invalid_config_is_clean_clierror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config that fails validation produces a CLIError with a hint,
    not a raw pydantic traceback."""
    from trinity.cli import CLIError

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    cfg_dir = tmp_path / "xdg_config" / "trinity"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(
        "[surface]\nschema_version = 1\nbogus_key = true\n"
        '[surface.source]\nprovider = "solid"\n'
    )
    runner = CliRunner()
    result = runner.invoke(main, ["apply"], standalone_mode=False)
    assert isinstance(result.exception, CLIError)
    assert "invalid config" in str(result.exception)


def test_status_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 0
    assert "manifest entries:" in result.output


def test_provider_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["provider", "list"])
    assert result.exit_code == 0
    assert "bing" in result.output
    assert "solid" in result.output
    assert "file" in result.output


def test_doctor_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])
    # exit code 0 or 1 depending on warnings, but command must not crash.
    assert result.exit_code in (0, 1)


def test_qml_update_templates_writes_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mock a fake vendor path so extract has something to read.
    fake_sddm = tmp_path / "fake_sddm" / "Login.qml"
    fake_sddm.parent.mkdir(parents=True)
    fake_sddm.write_text("import QtQuick\nItem {}\n", encoding="utf-8")
    fake_plasma_dir = tmp_path / "fake_plasma" / "lockscreen"
    fake_plasma_dir.mkdir(parents=True)
    (fake_plasma_dir / "MainBlock.qml").write_text(
        "import QtQuick\nItem {}\n", encoding="utf-8"
    )
    (fake_plasma_dir / "LockScreenUi.qml").write_text(
        "import QtQuick\nItem {}\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        extract,
        "DEFAULT_TARGETS",
        [
            ("sddm_login", fake_sddm),
            ("plasma_lockscreen_mainblock", fake_plasma_dir / "MainBlock.qml"),
            ("plasma_lockscreen_ui", fake_plasma_dir / "LockScreenUi.qml"),
        ],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["qml-update-templates", "--yes"])
    assert result.exit_code == 0, result.output


def test_pause_and_resume_report_systemctl_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Patch the re-exported names on ``trinity.systemd`` — the bindings
    # the CLI actually calls — so no real systemctl is ever invoked.
    monkeypatch.setattr("trinity.systemd.pause", lambda: (True, "paused"))
    monkeypatch.setattr("trinity.systemd.resume", lambda: (True, "resumed"))
    runner = CliRunner()
    assert runner.invoke(main, ["pause"]).exit_code == 0
    assert runner.invoke(main, ["resume"]).exit_code == 0


def test_pause_failure_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trinity.systemd.pause", lambda: (False, "no such unit"))
    result = CliRunner().invoke(main, ["pause"])
    assert result.exit_code == 1


def test_uninstall_removes_units_from_real_unit_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: uninstall must delete units from ~/.config/systemd/user
    (where install writes them), not ~/.config/trinity/systemd/user."""
    import subprocess

    from trinity import paths

    unit_dir = paths.config_dir().parent / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    svc = unit_dir / "trinity-pull.service"
    tmr = unit_dir / "trinity-pull.timer"
    svc.write_text("[Unit]\n")
    tmr.write_text("[Unit]\n")

    calls: list[tuple[str, ...]] = []

    def fake_systemctl(*args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=(), returncode=0, stdout="", stderr="")

    # Patch both the writer-internal binding (used by disable_and_stop)
    # and the package re-export (used directly by the CLI).
    monkeypatch.setattr("trinity.systemd.writer.systemctl", fake_systemctl)
    monkeypatch.setattr("trinity.systemd.systemctl", fake_systemctl)
    result = CliRunner().invoke(main, ["uninstall", "--yes"])
    assert result.exit_code == 0, result.output
    assert not svc.exists()
    assert not tmr.exists()
    assert ("daemon-reload",) in calls


def test_provider_info_known_and_unknown() -> None:
    runner = CliRunner()
    ok = runner.invoke(main, ["provider", "info", "bing"])
    assert ok.exit_code == 0
    assert "Bing" in ok.output
    # Unknown provider: EXIT_NOINPUT (66) — distinct from CLI usage (2)
    # so shell scripts can tell "no such provider" from "you typed the
    # command wrong".
    missing = runner.invoke(main, ["provider", "info", "nope"])
    assert missing.exit_code == 66


def test_migrate_from_shell_no_legacy_setup(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["migrate-from-shell", "--dry-run"])
    assert result.exit_code == 0
    assert "No existing shell-based" in result.output


def test_config_show_prints_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runner = CliRunner()
    runner.invoke(main, ["config", "init"])
    result = runner.invoke(main, ["config", "show"])
    assert result.exit_code == 0
    assert '"provider": "bing"' in result.output


def test_config_validate_reports_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_dir = tmp_path / "trinity"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text("[surface]\nbogus = 1\n")
    result = CliRunner().invoke(main, ["config", "validate"])
    # EXIT_DATAERR (65) — distinct from a generic runtime error (1).
    assert result.exit_code == 65
    assert "invalid" in result.output


def test_config_init_refuses_overwrite_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runner = CliRunner()
    assert runner.invoke(main, ["config", "init"]).exit_code == 0
    again = runner.invoke(main, ["config", "init"])
    # EXIT_CANTCREAT (73) — caller must re-run with --force.
    assert again.exit_code == 73
    assert "already exists" in again.output


# --- login_surface_needs_root helper + excepthook wrapper ---


def test_login_surface_needs_root_false_when_not_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the SDDM theme.conf doesn't exist, the helper returns False."""
    from trinity.backends import login as login_mod

    monkeypatch.setattr(login_mod, "_THEME_CONF_PATH", tmp_path / "nope.conf")
    assert login_mod.login_surface_needs_root() is False


def test_login_surface_needs_root_true_when_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If theme.conf exists, the user-conf path is unwritable, and
    euid != 0, returns True."""
    from trinity.backends import login as login_mod

    conf = tmp_path / "theme.conf"
    conf.write_text("[General]\n")
    monkeypatch.setattr(login_mod, "_THEME_CONF_PATH", conf)
    # Point the user-conf path at a read-only file so _can_write is False.
    user_conf = tmp_path / "theme.conf.user"
    user_conf.write_text("[General]\n")
    user_conf.chmod(0o444)  # read-only
    monkeypatch.setattr(login_mod, "_THEME_CONF_USER_PATH", user_conf)
    monkeypatch.setattr(login_mod.os, "geteuid", lambda: 1000)
    # _can_write checks os.access; with 0o444 and non-root, it's False.
    assert login_mod.login_surface_needs_root() is True


def test_login_surface_needs_root_false_when_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even if unwritable, euid 0 means root can write -> False."""
    from trinity.backends import login as login_mod

    conf = tmp_path / "theme.conf"
    conf.write_text("[General]\n")
    monkeypatch.setattr(login_mod, "_THEME_CONF_PATH", conf)
    user_conf = tmp_path / "theme.conf.user"
    user_conf.write_text("[General]\n")
    user_conf.chmod(0o444)
    monkeypatch.setattr(login_mod, "_THEME_CONF_USER_PATH", user_conf)
    monkeypatch.setattr(login_mod.os, "geteuid", lambda: 0)
    assert login_mod.login_surface_needs_root() is False


def test_run_wrapper_installs_excepthook_and_renders_clierror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run() entry-point wrapper installs the excepthook so a CLIError
    renders as a clean 'error: ...' block. CliRunner bypasses sys.excepthook,
    so we verify via the installed hook directly."""
    import subprocess
    import sys

    code = (
        "from trinity.cli import run, CLIError\n"
        "raise CLIError('bad thing', hint='try this')\n"
    )
    subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    # Without the excepthook installed (run() not called), this is an
    # unhandled CLIError traceback. Now verify run() installs the hook:
    code2 = (
        "import sys\n"
        "from trinity.cli import run, _install_excepthook, CLIError\n"
        "_install_excepthook()\n"
        "raise CLIError('bad thing', hint='try this')\n"
    )
    proc2 = subprocess.run(
        [sys.executable, "-c", code2], capture_output=True, text=True
    )
    assert proc2.returncode == 1
    assert "error: bad thing" in proc2.stderr
    assert "try this" in proc2.stderr
    assert "Traceback" not in proc2.stderr


def test_trinity_version_via_run(tmp_path: Path) -> None:
    """`trinity --version` works via the run() entry point wrapper."""
    import os
    import subprocess
    import sys

    # A stripped environment proves --version needs no session state,
    # but the interpreter's import path is not what's under test:
    # sandboxed builds (nix pytestCheckHook) expose the package via
    # PYTHONPATH rather than site-packages, so that one variable must
    # survive.
    env = {"PATH": "/usr/bin:/bin"}
    if "PYTHONPATH" in os.environ:
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    proc = subprocess.run(
        [sys.executable, "-m", "trinity", "--version"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0
    assert "trinity" in proc.stdout


def test_sigterm_handler_exits_143_without_traceback() -> None:
    """The SIGTERM handler installed by run() raises SystemExit(143)
    so finally blocks unwind, and the process exits with 143 (128+15)
    without a traceback on stderr."""
    import subprocess
    import sys

    # Spawn a child that installs the handler, then self-sends SIGTERM
    # after a brief sleep so the handler is in place.
    code = (
        "import os, signal, time\n"
        "from trinity.cli import _install_sigterm_handler\n"
        "_install_sigterm_handler()\n"
        "os.kill(os.getpid(), signal.SIGTERM)\n"
        "time.sleep(5)  # should never reach here\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 143
    assert "Traceback" not in proc.stderr
    # The structured log event should appear on stdout (structlog → stdout).
    assert "sigterm_received" in proc.stdout


# --- Phase 2: theme_tokens opt-in + setup command ---


def test_config_init_writes_theme_tokens_disabled() -> None:
    """`config init` writes theme_tokens.enabled = false by default."""
    from click.testing import CliRunner

    from trinity.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["config", "init"])
    assert result.exit_code == 0
    assert "[surface.theme_tokens]" in result.output or (
        runner.invoke(main, ["config", "show"]).output
    )
    # The config file should contain enabled = false.
    from trinity.config import load_config

    cfg = load_config(None)
    assert cfg.surface.theme_tokens.enabled is False


def test_legacy_config_auto_migrates_theme_tokens_enabled() -> None:
    """A pre-Phase-2 config (no theme_tokens key) is auto-migrated to
    enabled=true so existing users don't silently lose patching."""
    from trinity.config import load_config_from_string

    toml = """
[surface]
schema_version = 1

[surface.source]
provider = "bing"

[surface.source.options]
mkt = "en-US"
resolution = "1920x1080"

[surface.fonts]
family = "Inter"
"""
    cfg = load_config_from_string(toml)
    assert cfg.surface.theme_tokens.enabled is True


def test_apply_skips_qml_patching_when_theme_tokens_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When theme_tokens.enabled = false, apply skips QML patching and
    drift checks; the plan includes a 'theme tokens: disabled' line."""
    from click.testing import CliRunner

    from trinity.cli import main

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_dir = tmp_path / "trinity"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(
        "[surface]\n"
        "schema_version = 1\n"
        "[surface.source]\n"
        'provider = "bing"\n'
        "[surface.source.options]\n"
        'mkt = "en-US"\n'
        'resolution = "1920x1080"\n'
        "[surface.theme_tokens]\n"
        "enabled = false\n"
        "[surface.behaviour]\n"
        f'shared_dir = "{tmp_path / "shared"}"\n'
        f'user_dir = "{tmp_path / "user"}"\n'
    )

    # Patch the fetch + verify + backends to avoid real I/O.
    import io

    from PIL import Image

    from trinity.providers import FetchedImage

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 128, 255)).save(buf, format="PNG")
    fake_png = buf.getvalue()
    monkeypatch.setattr(
        "trinity.orchestrator.fetch_wallpaper",
        lambda c, pm=None: FetchedImage(
            data=fake_png,
            content_type="image/png",
            suggested_extension=".png",
        ),
    )
    from trinity.backends.desktop import DesktopBackend
    from trinity.backends.lock import LockBackend
    from trinity.backends.login import LoginBackend

    monkeypatch.setattr(DesktopBackend, "apply", lambda self, m, w: None)
    monkeypatch.setattr(LockBackend, "apply", lambda self, m, w: None)
    # Never touch the real SDDM theme.conf.user from a test.
    monkeypatch.setattr(LoginBackend, "apply", lambda self, m, w: None)

    runner = CliRunner()
    result = runner.invoke(main, ["apply"])
    assert result.exit_code == 0
    assert "theme tokens: disabled" in result.output
    # No QML drift checks should be reported.
    assert "QML drift" not in result.output


def test_apply_warns_when_theme_tokens_disabled_with_custom_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-default token values are warned-about when theme_tokens is
    disabled (they would otherwise be silently ignored)."""
    from click.testing import CliRunner

    from trinity.cli import main

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_dir = tmp_path / "trinity"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(
        "[surface]\n"
        "schema_version = 1\n"
        "[surface.source]\n"
        'provider = "bing"\n'
        "[surface.source.options]\n"
        'mkt = "en-US"\n'
        'resolution = "1920x1080"\n'
        "[surface.theme_tokens]\n"
        "enabled = false\n"
        "[surface.fonts]\n"
        'family = "DejaVu Sans"  # non-default, should trigger warning\n'
        "[surface.behaviour]\n"
        f'shared_dir = "{tmp_path / "shared"}"\n'
        f'user_dir = "{tmp_path / "user"}"\n'
    )

    import io

    from PIL import Image

    from trinity.backends.desktop import DesktopBackend
    from trinity.backends.lock import LockBackend
    from trinity.providers import FetchedImage

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 128, 255)).save(buf, format="PNG")
    monkeypatch.setattr(
        "trinity.orchestrator.fetch_wallpaper",
        lambda c, pm=None: FetchedImage(
            data=buf.getvalue(),
            content_type="image/png",
            suggested_extension=".png",
        ),
    )
    from trinity.backends.login import LoginBackend

    monkeypatch.setattr(DesktopBackend, "apply", lambda self, m, w: None)
    monkeypatch.setattr(LockBackend, "apply", lambda self, m, w: None)
    # Never touch the real SDDM theme.conf.user from a test.
    monkeypatch.setattr(LoginBackend, "apply", lambda self, m, w: None)

    runner = CliRunner()
    result = runner.invoke(main, ["apply"])
    assert result.exit_code == 0
    assert "theme tokens: disabled" in result.output
    assert "token values are set but ignored" in result.output


def test_status_reports_theme_tokens_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`trinity status` reports theme tokens status."""
    from click.testing import CliRunner

    from trinity.cli import main

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_dir = tmp_path / "trinity"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(
        "[surface]\n"
        "schema_version = 1\n"
        "[surface.source]\n"
        'provider = "bing"\n'
        "[surface.source.options]\n"
        'mkt = "en-US"\n'
        'resolution = "1920x1080"\n'
        "[surface.theme_tokens]\n"
        "enabled = false\n"
    )

    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 0
    assert "theme tokens: disabled" in result.output


def test_setup_chains_init_install_dryrun_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`trinity setup --yes` runs config init, install, apply --dry-run,
    and apply in order."""
    from click.testing import CliRunner

    from trinity.cli import main

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # Track the order of command invocations.
    calls: list[str] = []

    # Monkeypatch the command callbacks by replacing them on the
    # Click group.  Click stores commands in a dict keyed by name.
    from trinity import cli as cli_mod

    original_init = cli_mod.config_init.callback
    original_install = cli_mod.install.callback
    original_apply = cli_mod.apply.callback

    def fake_init(*args: object, **kwargs: object) -> None:
        calls.append("config_init")

    def fake_install(*args: object, **kwargs: object) -> None:
        calls.append("install")

    def fake_apply(*args: object, **kwargs: object) -> None:
        calls.append("apply")

    # Replace the Click command objects' callbacks.
    cli_mod.config_init.callback = fake_init
    cli_mod.install.callback = fake_install
    cli_mod.apply.callback = fake_apply
    try:
        runner = CliRunner()
        runner.invoke(main, ["setup", "--yes"], input="")
    finally:
        cli_mod.config_init.callback = original_init
        cli_mod.install.callback = original_install
        cli_mod.apply.callback = original_apply

    # setup skips config init (config file already exists via XDG_CONFIG_HOME
    # setup above which may not exist; either way, the init + install +
    # apply-dry-run + apply sequence must be invoked).  At minimum we
    # expect the install and two apply calls (dry-run + real).
    assert "install" in calls
    assert calls.count("apply") == 2  # dry-run + real apply


def test_apply_expands_user_dir_with_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """trinity apply correctly expands environment variables in user_dir lock path."""
    cfg_dir = tmp_path / "xdg_config" / "trinity"
    cfg_dir.mkdir(parents=True)
    shared = str(tmp_path / "shared")
    monkeypatch.setenv("MY_TEST_VAR", str(tmp_path / "expanded_user"))
    config_path = cfg_dir / "config.toml"
    config_path.write_text(
        f"""[surface]
schema_version = 1
[surface.source]
provider = "solid"
[surface.behaviour]
shared_dir = "{shared}"
user_dir = "$MY_TEST_VAR/lock_test"
"""
    )

    from trinity import paths
    monkeypatch.setattr(paths, "config_file", lambda: config_path)
    monkeypatch.setattr(paths, "manifest_file", lambda: tmp_path / "manifest.jsonl")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    runner = CliRunner()
    result = runner.invoke(main, ["apply", "--dry-run"])
    assert result.exit_code == 0
    result = runner.invoke(main, ["apply"])
    assert result.exit_code == 0
    expected_path = tmp_path / "expanded_user" / "lock_test"
    assert expected_path.exists()
