"""Phase 3 tool tests: each tool runs through the sandbox; shell cwd confinement."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_code.permissions import RiskLevel
from jarvis_code.sandbox import Sandbox
from jarvis_code.tools import ToolContext, build_default_registry
from jarvis_code.tools.shell_tool import (
    ShellCommandBlocked,
    assess_shell_risk,
    screen_shell_command,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def main():\n    return 42\n")
    (tmp_path / "README.md").write_text("# demo\nhello world\n")
    return tmp_path


@pytest.fixture
def ctx(project: Path) -> ToolContext:
    return ToolContext(sandbox=Sandbox(project), shell_timeout=20.0)


@pytest.fixture
def registry():
    return build_default_registry()


def run(registry, ctx, name, **args):
    return registry.get(name).run(ctx, args)


# -- registry ---------------------------------------------------------------


def test_registry_has_full_tool_set(registry) -> None:
    expected = {
        "list_files", "read_file", "write_file", "edit_file", "delete_file",
        "search_files", "grep_text", "run_shell", "git_diff", "git_status",
        "create_directory", "move_file", "spawn_subagent",
    }
    assert set(registry.names()) == expected


def test_registry_specs_have_schema(registry) -> None:
    for spec in registry.specs():
        assert "name" in spec and "description" in spec and "parameters" in spec


# -- fs tools ---------------------------------------------------------------


def test_list_files(registry, ctx) -> None:
    res = run(registry, ctx, "list_files", path=".")
    assert res.ok
    assert "src/" in res.output and "README.md" in res.output


def test_read_file(registry, ctx) -> None:
    res = run(registry, ctx, "read_file", path="src/app.py")
    assert res.ok and "return 42" in res.output


def test_read_out_of_root_blocked(registry, ctx) -> None:
    res = run(registry, ctx, "read_file", path="../../../etc/passwd")
    assert not res.ok and res.error


def test_read_file_paginates_large_file(registry, ctx, project: Path) -> None:
    (project / "big.txt").write_text("\n".join(f"line{i}" for i in range(1, 5001)) + "\n")

    # Default read returns the first chunk and a continuation hint (no shell tail).
    res = run(registry, ctx, "read_file", path="big.txt")
    assert res.ok
    assert "line1" in res.output and "line2000" in res.output
    assert "line2001" not in res.output
    assert "offset=2001 to continue" in res.output
    assert res.metadata["lines"] == 5000

    # Continue from where it left off via the tool itself.
    res2 = run(registry, ctx, "read_file", path="big.txt", offset=2001, limit=10)
    assert res2.ok
    assert "line2001" in res2.output and "line2010" in res2.output
    assert "line2011" not in res2.output


def test_write_and_edit(registry, ctx, project: Path) -> None:
    w = run(registry, ctx, "write_file", path="notes.txt", content="one\ntwo\n")
    assert w.ok and (project / "notes.txt").read_text() == "one\ntwo\n"
    assert w.metadata["files_changed"] == ["notes.txt"]

    e = run(registry, ctx, "edit_file", path="notes.txt", old_string="two", new_string="three")
    assert e.ok and (project / "notes.txt").read_text() == "one\nthree\n"
    assert "diff" in e.metadata


def test_edit_nonunique_requires_replace_all(registry, ctx) -> None:
    run(registry, ctx, "write_file", path="d.txt", content="x x x")
    res = run(registry, ctx, "edit_file", path="d.txt", old_string="x", new_string="y")
    assert not res.ok and "unique" in res.error
    ok = run(registry, ctx, "edit_file", path="d.txt", old_string="x", new_string="y", replace_all=True)
    assert ok.ok


def test_delete_and_create_directory(registry, ctx, project: Path) -> None:
    run(registry, ctx, "create_directory", path="build/tmp")
    assert (project / "build" / "tmp").is_dir()
    run(registry, ctx, "delete_file", path="build")
    assert not (project / "build").exists()


def test_move_file(registry, ctx, project: Path) -> None:
    res = run(registry, ctx, "move_file", src="README.md", dst="docs/README.md")
    assert res.ok
    assert not (project / "README.md").exists()
    assert (project / "docs" / "README.md").exists()


def test_move_out_of_root_blocked(registry, ctx) -> None:
    res = run(registry, ctx, "move_file", src="README.md", dst="../escaped.md")
    assert not res.ok


# -- search tools -----------------------------------------------------------


def test_search_files(registry, ctx) -> None:
    res = run(registry, ctx, "search_files", pattern="*.py")
    assert res.ok and "src/app.py" in res.output


def test_grep_text(registry, ctx) -> None:
    res = run(registry, ctx, "grep_text", pattern="hello")
    assert res.ok and "README.md" in res.output


# -- shell screening & risk -------------------------------------------------


def test_screen_blocks_rm_rf_root() -> None:
    with pytest.raises(ShellCommandBlocked):
        screen_shell_command("rm -rf /")


def test_screen_blocks_fork_bomb() -> None:
    with pytest.raises(ShellCommandBlocked):
        screen_shell_command(":(){ :|:& };:")


def test_screen_allows_normal_command() -> None:
    screen_shell_command("pytest -q")  # no exception


def test_risk_levels() -> None:
    assert assess_shell_risk("ls") == RiskLevel.LOW
    assert assess_shell_risk("rm -rf build") == RiskLevel.HIGH
    assert assess_shell_risk("pip install requests") == RiskLevel.MEDIUM
    assert assess_shell_risk("sudo whoami") == RiskLevel.HIGH


def test_run_shell_blocked_command_reports_blocked(registry, ctx) -> None:
    res = run(registry, ctx, "run_shell", command="rm -rf /")
    assert not res.ok and res.metadata.get("blocked") is True


# -- shell cwd confinement (the key invariant) ------------------------------


def test_run_shell_runs_in_sandbox(registry, ctx, project: Path) -> None:
    res = run(registry, ctx, "run_shell", command="pwd")
    assert res.ok
    assert res.output.strip() == str(project.resolve())


def test_run_shell_cwd_subdir_ok(registry, ctx, project: Path) -> None:
    res = run(registry, ctx, "run_shell", command="pwd", cwd="src")
    assert res.ok and res.output.strip() == str((project / "src").resolve())


def test_run_shell_cwd_outside_root_rejected(registry, ctx) -> None:
    res = run(registry, ctx, "run_shell", command="pwd", cwd="..")
    assert not res.ok and res.error


def test_run_shell_cwd_absolute_outside_rejected(registry, ctx) -> None:
    res = run(registry, ctx, "run_shell", command="pwd", cwd="/tmp")
    assert not res.ok and res.error


# -- subagent stub ----------------------------------------------------------


def test_spawn_subagent_deferred_without_orchestrator(registry, ctx) -> None:
    res = run(registry, ctx, "spawn_subagent", agent_type="explorer", task="map the repo")
    assert res.ok and res.metadata.get("deferred") is True


def test_spawn_subagent_rejects_unknown_type(registry, ctx) -> None:
    res = run(registry, ctx, "spawn_subagent", agent_type="wizard", task="x")
    assert not res.ok
