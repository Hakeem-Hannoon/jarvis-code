"""Phase 6 CLI tests: init tree, doctor (never prints a key), routing, config."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from jarvis_code.cli.main import app, inject_default_command
from jarvis_code.utils import logging as jlog

runner = CliRunner()

KEY = "sk-secret-DOCTOR-1234567890"


@pytest.fixture(autouse=True)
def _clean_secrets():
    jlog.clear_secrets()
    yield
    jlog.clear_secrets()


def _config_with_key(base: Path) -> None:
    cfg = base / ".jarvis"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.toml").write_text(
        f'default_provider = "anthropic"\n\n'
        f'[providers.anthropic]\napi_key = "{KEY}"\nmodel = "claude-opus-4-8"\n'
    )


# -- default-command routing ------------------------------------------------


def test_inject_leaves_subcommands_alone() -> None:
    assert inject_default_command(["init"]) == ["init"]
    assert inject_default_command(["doctor"]) == ["doctor"]
    assert inject_default_command(["config", "validate"]) == ["config", "validate"]


def test_inject_leaves_help_and_version() -> None:
    assert inject_default_command(["--help"]) == ["--help"]
    assert inject_default_command(["--version"]) == ["--version"]
    assert inject_default_command([]) == []


def test_inject_routes_bare_task_to_run() -> None:
    assert inject_default_command(["explain", "this"]) == ["run", "explain", "this"]
    assert inject_default_command(["fix the bug"]) == ["run", "fix the bug"]


def test_inject_accounts_for_value_options() -> None:
    assert inject_default_command(["--mode", "plan", "explain"]) == [
        "--mode", "plan", "run", "explain",
    ]
    assert inject_default_command(["--mode", "plan", "init"]) == ["--mode", "plan", "init"]
    assert inject_default_command(["--sandbox", "/x", "do", "it"]) == [
        "--sandbox", "/x", "run", "do", "it",
    ]
    assert inject_default_command(["--mode=plan", "task"]) == ["--mode=plan", "run", "task"]
    # --voice takes a value, so its value is not mistaken for a task token
    assert inject_default_command(["--voice", "false", "explain"]) == [
        "--voice", "false", "run", "explain",
    ]
    assert inject_default_command(["--voice", "true", "doctor"]) == ["--voice", "true", "doctor"]


# -- --voice flag -----------------------------------------------------------


def test_parse_voice_values() -> None:
    from jarvis_code.cli.options import parse_voice

    assert parse_voice(None) is None  # unset -> use config
    assert parse_voice("true") is True and parse_voice("on") is True
    assert parse_voice("false") is False and parse_voice("off") is False
    with pytest.raises(ValueError):
        parse_voice("maybe")


def test_voice_flag_rejects_bad_value(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--voice", "maybe", "doctor"])
    assert result.exit_code == 2
    assert "invalid --voice" in result.output


# -- jarvis init ------------------------------------------------------------


def test_init_creates_ignored_tree(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--sandbox", str(tmp_path), "init"])
    assert result.exit_code == 0
    assert (tmp_path / ".jarvis" / "config.toml").is_file()
    assert (tmp_path / ".jarvis" / "sessions").is_dir()
    assert (tmp_path / ".jarvis" / "voices").is_dir()
    # The whole .jarvis tree is git-ignored by default.
    assert (tmp_path / ".jarvis" / ".gitignore").read_text() == "*\n"


def test_init_does_not_overwrite_existing_config(tmp_path: Path) -> None:
    _config_with_key(tmp_path)
    result = runner.invoke(app, ["--sandbox", str(tmp_path), "init"])
    assert result.exit_code == 0
    assert "already exists" in result.output
    # the existing key-bearing config is preserved
    assert KEY in (tmp_path / ".jarvis" / "config.toml").read_text()


# -- jarvis doctor ----------------------------------------------------------


def test_doctor_runs_and_never_prints_key(tmp_path: Path) -> None:
    _config_with_key(tmp_path)
    result = runner.invoke(app, ["--sandbox", str(tmp_path), "doctor"])
    assert result.exit_code == 0
    assert KEY not in result.output  # the critical assertion
    assert "jarvis doctor" in result.output
    # boolean presence is shown instead of the value
    assert "present" in result.output


def test_doctor_works_without_config(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--sandbox", str(tmp_path), "doctor"])
    assert result.exit_code == 0
    assert "run: jarvis init" in result.output


# -- config path / validate -------------------------------------------------


def test_config_path(tmp_path: Path) -> None:
    _config_with_key(tmp_path)
    result = runner.invoke(app, ["--sandbox", str(tmp_path), "config", "path"])
    assert result.exit_code == 0
    assert str(tmp_path / ".jarvis" / "config.toml") in result.output


def test_config_validate_clean(tmp_path: Path) -> None:
    _config_with_key(tmp_path)
    result = runner.invoke(app, ["--sandbox", str(tmp_path), "config", "validate"])
    assert result.exit_code == 0
    assert "valid" in result.output.lower()


# -- friendly no-config error -----------------------------------------------


def test_run_task_no_config_friendly_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--sandbox", str(tmp_path), "run", "explain the project"])
    assert result.exit_code == 1
    assert "No provider config found." in result.output
    assert "jarvis init" in result.output


# -- voices -----------------------------------------------------------------


def test_global_config_fallback(tmp_path: Path, monkeypatch) -> None:
    # A project with no local config falls back to the per-user global config.
    from jarvis_code.config import find_config_path, global_config_path

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    gpath = global_config_path()
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text(
        f'default_provider = "anthropic"\n[providers.anthropic]\napi_key = "{KEY}"\nmodel = "m"\n'
    )
    project = tmp_path / "empty-project"
    project.mkdir()
    assert find_config_path(project) == gpath  # found via global fallback


def test_local_config_overrides_global(tmp_path: Path, monkeypatch) -> None:
    from jarvis_code.config import find_config_path, global_config_path

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    gpath = global_config_path()
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text('default_provider = "anthropic"\n[providers.anthropic]\napi_key=""\nmodel="m"\n')
    project = tmp_path / "proj"
    _config_with_key(project)
    assert find_config_path(project) == project / ".jarvis" / "config.toml"


def test_init_global_creates_user_config(tmp_path: Path, monkeypatch) -> None:
    from jarvis_code.config import global_config_path

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    result = runner.invoke(app, ["init", "--global"])
    assert result.exit_code == 0
    assert global_config_path().is_file()
    assert "[providers.anthropic]" in global_config_path().read_text()


def test_voices_list_runs(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--sandbox", str(tmp_path), "voices", "list"])
    assert result.exit_code == 0
    assert "noop" in result.output.lower()
