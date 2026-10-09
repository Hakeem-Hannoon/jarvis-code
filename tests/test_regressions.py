"""Regression tests for bugs found in the audit (one section per fix)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_code.agent import assemble_runtime
from jarvis_code.agent.loop import RunStatus
from jarvis_code.config import JarvisConfig
from jarvis_code.models import ModelResponse, ToolCall
from jarvis_code.permissions import OutcomeKind, PermissionManager, PermissionMode
from jarvis_code.sandbox import Sandbox
from jarvis_code.tools import ToolContext, ToolExecutor, build_default_registry


class ScriptedProvider:
    """Returns canned responses in order and records what it was sent."""

    name = "scripted"
    model = "test"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list] = []

    def complete(self, messages, tools=None) -> ModelResponse:
        self.calls.append(list(messages))
        return self._responses.pop(0)


def _config() -> JarvisConfig:
    return JarvisConfig.model_validate(
        {
            "default_provider": "anthropic",
            "providers": {"anthropic": {"api_key": "sk-test-000000", "model": "m"}},
            "voice": {"enabled": False},
        }
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("hello\nworld\n")
    return tmp_path


@pytest.fixture
def sandbox(root: Path) -> Sandbox:
    return Sandbox(root)


@pytest.fixture
def executor(sandbox: Sandbox) -> ToolExecutor:
    return ToolExecutor(
        build_default_registry(),
        PermissionManager(PermissionMode.DANGEROUS),
        ToolContext(sandbox=sandbox, shell_timeout=10.0),
    )


# -- a tool failure is a tool result, never a crashed run --------------------


def test_missing_argument_is_a_failed_result_not_an_exception(executor) -> None:
    out = executor.execute("read_file", {})
    assert out.outcome is OutcomeKind.ALLOWED
    assert out.result is not None and not out.result.ok
    assert "missing required argument 'path'" in (out.result.error or "")


def test_non_object_arguments_are_rejected_cleanly(executor) -> None:
    out = executor.execute("read_file", ["not", "a", "dict"])  # type: ignore[arg-type]
    assert out.result is not None and not out.result.ok
    assert executor.preview("read_file", "nope") is None  # type: ignore[arg-type]


def test_overwriting_a_binary_file_does_not_crash(executor, root: Path) -> None:
    (root / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
    out = executor.execute("write_file", {"path": "blob.bin", "content": "text now"})
    assert out.result is not None and out.result.ok
    assert (root / "blob.bin").read_text() == "text now"


async def test_run_survives_a_tool_call_with_bad_arguments(sandbox: Sandbox) -> None:
    provider = ScriptedProvider(
        [
            ModelResponse(tool_calls=[ToolCall("c1", "read_file", {})]),
            ModelResponse(text="recovered"),
        ]
    )
    runtime = assemble_runtime(
        config=_config(), sandbox=sandbox, mode=PermissionMode.PLAN, provider=provider
    )
    result = await runtime.run("read something")
    assert result.status is RunStatus.COMPLETE
    assert result.final_text == "recovered"
    # The model was told what went wrong so it could correct itself.
    assert "missing required argument" in provider.calls[1][-1].content


# -- edit_file ---------------------------------------------------------------


def test_edit_rejects_empty_old_string(executor, root: Path) -> None:
    # "".count() matches between every character; replace_all used to splice the
    # replacement through the entire file.
    out = executor.execute(
        "edit_file",
        {"path": "a.txt", "old_string": "", "new_string": "X", "replace_all": True},
    )
    assert out.result is not None and not out.result.ok
    assert (root / "a.txt").read_text() == "hello\nworld\n"  # untouched


def test_edit_replace_all_string_false_is_false(executor, root: Path) -> None:
    (root / "r.txt").write_text("a a a")
    out = executor.execute(
        "edit_file",
        {"path": "r.txt", "old_string": "a", "new_string": "b", "replace_all": "false"},
    )
    assert out.result is not None and not out.result.ok  # 3 matches, not unique
    assert (root / "r.txt").read_text() == "a a a"


def test_edit_noop_is_rejected(executor) -> None:
    out = executor.execute(
        "edit_file", {"path": "a.txt", "old_string": "hello", "new_string": "hello"}
    )
    assert out.result is not None and not out.result.ok


# -- symlinks: act on the link entry, never its target ------------------------


def test_delete_removes_an_in_root_symlink_that_points_outside(
    executor, root: Path, tmp_path_factory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "keep.txt"
    outside.write_text("do not touch")
    (root / "link_out").symlink_to(outside)
    out = executor.execute("delete_file", {"path": "link_out"})
    assert out.result is not None and out.result.ok
    assert not (root / "link_out").is_symlink()  # the link is gone
    assert outside.read_text() == "do not touch"  # the target is untouched


def test_list_files_names_the_symlink_not_its_target(executor, root: Path) -> None:
    (root / "sub").mkdir()
    (root / "sub" / "f.txt").write_text("x")
    (root / "alias.txt").symlink_to(root / "sub" / "f.txt")
    names = executor.execute("list_files", {"path": "."}).result.output.splitlines()
    assert "alias.txt" in names
    assert "sub/f.txt" not in names  # used to show the target's path instead


# -- move_file ----------------------------------------------------------------


def test_move_into_a_directory_reports_the_real_destination(executor, root: Path) -> None:
    (root / "d").mkdir()
    (root / "m.txt").write_text("m")
    out = executor.execute("move_file", {"src": "m.txt", "dst": "d"})
    assert out.result is not None and out.result.ok
    assert out.result.metadata["files_changed"] == ["m.txt", "d/m.txt"]
    assert (root / "d" / "m.txt").read_text() == "m"


def test_move_refuses_to_clobber_an_existing_file(executor, root: Path) -> None:
    (root / "b.txt").write_text("precious")
    out = executor.execute("move_file", {"src": "a.txt", "dst": "b.txt"})
    assert out.result is not None and not out.result.ok
    assert (root / "b.txt").read_text() == "precious"
    assert (root / "a.txt").exists()


def test_read_file_offset_past_the_end_is_an_error(executor) -> None:
    out = executor.execute("read_file", {"path": "a.txt", "offset": 50})
    assert out.result is not None and not out.result.ok
    assert "past the end" in (out.result.error or "")


# -- search -------------------------------------------------------------------


def _executor_at(path: Path) -> ToolExecutor:
    return ToolExecutor(
        build_default_registry(),
        PermissionManager(PermissionMode.PLAN),
        ToolContext(sandbox=Sandbox(path)),
    )


def test_search_works_when_the_sandbox_lives_under_a_skipped_dir_name(tmp_path: Path) -> None:
    # The skip list (venv, node_modules, ...) used to match against the ABSOLUTE
    # path, so a project anywhere below a dir named "venv" searched nothing.
    proj = tmp_path / "venv" / "proj"
    proj.mkdir(parents=True)
    (proj / "x.py").write_text("needle = 1\n")
    ex = _executor_at(proj)
    assert ex.execute("search_files", {"pattern": "*.py"}).result.output == "x.py"
    assert "x.py:1:" in ex.execute("grep_text", {"pattern": "needle"}).result.output


def test_search_still_skips_noise_dirs_inside_the_sandbox(tmp_path: Path) -> None:
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "i.js").write_text("needle")
    (tmp_path / "app.js").write_text("needle")
    ex = _executor_at(tmp_path)
    assert ex.execute("search_files", {"pattern": "*.js"}).result.output == "app.js"
    assert "node_modules" not in ex.execute("grep_text", {"pattern": "needle"}).result.output


def test_search_files_accepts_path_globs(tmp_path: Path) -> None:
    (tmp_path / "src" / "deep").mkdir(parents=True)
    (tmp_path / "src" / "k.py").write_text("k")
    (tmp_path / "src" / "deep" / "d.py").write_text("d")
    (tmp_path / "top.py").write_text("t")
    ex = _executor_at(tmp_path)
    out = ex.execute("search_files", {"pattern": "src/*.py"}).result.output.splitlines()
    assert "src/k.py" in out and "top.py" not in out
    everywhere = ex.execute("search_files", {"pattern": "**/*.py"}).result.output.splitlines()
    assert {"top.py", "src/k.py", "src/deep/d.py"} <= set(everywhere)


def test_grep_accepts_a_single_file_path(tmp_path: Path) -> None:
    (tmp_path / "one.txt").write_text("alpha\nbeta\n")
    (tmp_path / "two.txt").write_text("beta\n")
    out = _executor_at(tmp_path).execute(
        "grep_text", {"pattern": "beta", "path": "one.txt"}
    ).result.output
    assert out == "one.txt:2: beta"
