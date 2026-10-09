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


# -- run_shell ----------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        'grep -rn "shutdown" .',
        'echo "do not reboot the server"',
        "git log --grep=halt",
        'git commit -m "fix poweroff handling"',
        "dd if=/dev/zero of=/dev/null bs=1M count=1",
        "rm -rf build",
        "cat mkfs_notes.txt",
    ],
)
def test_screening_does_not_refuse_harmless_commands(command: str) -> None:
    from jarvis_code.tools.shell_tool import screen_shell_command

    screen_shell_command(command)  # must not raise


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -Rf /",
        "rm -r -f /",
        "rm -rf --no-preserve-root /",
        "rm -rf ~/",
        'rm -rf "$HOME"',
        "sudo rm -rf /*",
        "sudo -u root rm -rf /",
        "cd /tmp && rm -rf ~",
        "echo hi\nrm -rf /",
        "sh -c 'rm -rf /'",
        "FOO=1 rm --recursive --force /",
        "shutdown -h now",
        "ls; sudo reboot",
        "systemctl poweroff",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        "echo x > /dev/sda",
        ":(){ :|:& };:",
    ],
)
def test_screening_blocks_catastrophic_spellings(command: str) -> None:
    from jarvis_code.tools.shell_tool import ShellCommandBlocked, screen_shell_command

    with pytest.raises(ShellCommandBlocked):
        screen_shell_command(command)


def test_shell_reads_eof_from_stdin_instead_of_the_terminal(executor) -> None:
    # `cat` with inherited stdin would sit on the user's terminal until timeout.
    out = executor.execute("run_shell", {"command": "cat; echo after"})
    assert out.result is not None and out.result.ok
    assert out.result.output.strip() == "after"


def test_shell_timeout_kills_the_whole_process_tree(sandbox: Sandbox, root: Path) -> None:
    import os
    import time

    ex = ToolExecutor(
        build_default_registry(),
        PermissionManager(PermissionMode.DANGEROUS),
        ToolContext(sandbox=sandbox, shell_timeout=1.0),
    )
    # A background child that would outlive the shell if only the shell is killed.
    out = ex.execute(
        "run_shell",
        {"command": "(sleep 30 & echo $! > child.pid; echo started; wait)"},
    )
    assert out.result is not None and not out.result.ok
    assert "timed out" in (out.result.error or "")
    assert "started" in out.result.output  # partial output survives the timeout
    pid = int((root / "child.pid").read_text())
    for _ in range(50):  # the kill is asynchronous; give it a moment
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(pid, 9)
        pytest.fail("background child survived the timeout")


async def test_model_sees_the_output_of_a_failing_command(sandbox: Sandbox) -> None:
    provider = ScriptedProvider(
        [
            ModelResponse(
                tool_calls=[
                    ToolCall("c1", "run_shell", {"command": "echo 3 tests failed; exit 1"})
                ]
            ),
            ModelResponse(text="saw it"),
        ]
    )
    runtime = assemble_runtime(
        config=_config(), sandbox=sandbox, mode=PermissionMode.DANGEROUS, provider=provider
    )
    await runtime.run("run the tests")
    tool_result = provider.calls[1][-1].content
    assert "exit code 1" in tool_result
    assert "3 tests failed" in tool_result  # used to be dropped entirely


# -- context: compaction + repair --------------------------------------------


def _pairing_is_valid(messages) -> bool:
    """Every TOOL result directly answers a call from the preceding assistant turn."""
    from jarvis_code.models import Role

    open_ids: list[str] = []
    for m in messages:
        if m.role == Role.TOOL:
            if m.tool_call_id not in open_ids:
                return False
            open_ids.remove(m.tool_call_id)
        else:
            if open_ids:
                return False  # a call was never answered
            open_ids = [tc.id for tc in m.tool_calls] if m.role == Role.ASSISTANT else []
    return not open_ids


def test_compaction_never_orphans_a_tool_result() -> None:
    from jarvis_code.agent.context import ConversationContext
    from jarvis_code.agent.state import RunState

    ctx = ConversationContext("sys", max_chars=10, keep_recent=6)
    ctx.add_user("task")
    # One call with 3 results, then one with 2: the naive "last 6 messages"
    # window starts in the middle of the first batch's results.
    ctx.add_assistant(ModelResponse(tool_calls=[ToolCall(f"a{i}", "read_file", {}) for i in range(3)]))
    for i in range(3):
        ctx.add_tool_result(f"a{i}", "read_file", "x" * 40)
    ctx.add_assistant(ModelResponse(tool_calls=[ToolCall(f"b{i}", "read_file", {}) for i in range(2)]))
    for i in range(2):
        ctx.add_tool_result(f"b{i}", "read_file", "y" * 40)

    assert ctx.compact(RunState(goal="g"), force=True)
    assert _pairing_is_valid(ctx.messages[1:])  # [0] is the summary


def test_close_dangling_tool_calls_answers_unanswered_calls_and_drops_orphans() -> None:
    from jarvis_code.agent.context import ConversationContext

    ctx = ConversationContext("sys")
    ctx.add_user("go")
    ctx.add_tool_result("ghost", "read_file", "orphan")  # no call asked for this
    ctx.add_assistant(ModelResponse(tool_calls=[ToolCall("c1", "a", {}), ToolCall("c2", "b", {})]))
    ctx.add_tool_result("c1", "a", "done")  # c2 never answered (interrupted)
    assert ctx.close_dangling_tool_calls() == 2
    assert _pairing_is_valid(ctx.messages)
    assert ctx.close_dangling_tool_calls() == 0  # idempotent


# -- loop: stop / interrupt / truncation -------------------------------------


class _SlowProvider:
    name = "slow"
    model = "test"

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def complete(self, messages, tools=None) -> ModelResponse:
        import time

        time.sleep(self.seconds)
        return ModelResponse(text="too late")


async def test_a_stop_in_one_turn_does_not_brick_the_next(sandbox: Sandbox) -> None:
    provider = ScriptedProvider([ModelResponse(text="second turn ran")])
    runtime = assemble_runtime(
        config=_config(), sandbox=sandbox, mode=PermissionMode.PLAN, provider=provider
    )
    runtime.steer("stop")
    first = await runtime.run("first")
    assert first.status is RunStatus.ABORTED
    # The runtime is reused across chat turns: the abort flag used to stick.
    second = await runtime.run("second")
    assert second.status is RunStatus.COMPLETE
    assert second.final_text == "second turn ran"


async def test_interrupt_abandons_a_slow_model_call_immediately(sandbox: Sandbox) -> None:
    import asyncio
    import time

    runtime = assemble_runtime(
        config=_config(), sandbox=sandbox, mode=PermissionMode.PLAN, provider=_SlowProvider(30)
    )
    asyncio.get_running_loop().call_later(0.2, runtime.interrupt.set)
    started = time.monotonic()
    result = await runtime.run("think forever")
    assert result.status is RunStatus.ABORTED
    assert time.monotonic() - started < 5  # not the provider's 30 seconds


async def test_an_interrupt_only_stops_its_own_turn(sandbox: Sandbox) -> None:
    import asyncio

    runtime = assemble_runtime(
        config=_config(), sandbox=sandbox, mode=PermissionMode.PLAN, provider=_SlowProvider(30)
    )
    asyncio.get_running_loop().call_later(0.2, runtime.interrupt.set)
    assert (await runtime.run("think forever")).status is RunStatus.ABORTED
    # The flag is reset per turn (it once leaked and aborted every later turn).
    runtime.provider = ScriptedProvider([ModelResponse(text="back to work")])
    again = await runtime.run("carry on")
    assert again.status is RunStatus.COMPLETE


async def test_typed_stop_lands_during_the_model_call(sandbox: Sandbox) -> None:
    import asyncio
    import time

    runtime = assemble_runtime(
        config=_config(), sandbox=sandbox, mode=PermissionMode.PLAN, provider=_SlowProvider(30)
    )
    asyncio.get_running_loop().call_later(0.2, runtime.steer, "stop")
    started = time.monotonic()
    result = await runtime.run("think forever")
    assert result.status is RunStatus.ABORTED
    assert time.monotonic() - started < 5


async def test_keyboard_interrupt_in_a_tool_stops_the_turn_not_the_session(
    sandbox: Sandbox,
) -> None:
    from jarvis_code.permissions import PermissionResponse

    class _CtrlCPrompter:
        def request_permission(self, request) -> PermissionResponse:
            raise KeyboardInterrupt  # Ctrl-C at the approval prompt

    provider = ScriptedProvider(
        [
            ModelResponse(
                tool_calls=[
                    ToolCall("c1", "write_file", {"path": "x.txt", "content": "x"}),
                    ToolCall("c2", "write_file", {"path": "y.txt", "content": "y"}),
                ]
            ),
            ModelResponse(text="next turn fine"),
        ]
    )
    runtime = assemble_runtime(
        config=_config(), sandbox=sandbox, mode=PermissionMode.AUTO,
        provider=provider, prompter=_CtrlCPrompter(),
    )
    result = await runtime.run("write things")
    assert result.status is RunStatus.ABORTED
    assert _pairing_is_valid(runtime.context.messages)  # both calls were answered
    again = await runtime.run("carry on")
    assert again.status is RunStatus.COMPLETE


async def test_truncated_tool_call_is_not_executed(sandbox: Sandbox, root: Path) -> None:
    provider = ScriptedProvider(
        [
            ModelResponse(
                tool_calls=[ToolCall("c1", "write_file", {"path": "big.txt", "content": "half a fi"})],
                stop_reason="max_tokens",
            ),
            ModelResponse(text="ok, smaller pieces"),
        ]
    )
    runtime = assemble_runtime(
        config=_config(), sandbox=sandbox, mode=PermissionMode.DANGEROUS, provider=provider
    )
    result = await runtime.run("write a big file")
    assert result.status is RunStatus.COMPLETE
    assert not (root / "big.txt").exists()  # the cut-off write never happened
    assert "output-token limit" in provider.calls[1][-1].content


async def test_unexpected_provider_exception_is_an_error_result(sandbox: Sandbox) -> None:
    class _Broken:
        name = "broken"
        model = "test"

        def complete(self, messages, tools=None):
            raise KeyError("choices")

    runtime = assemble_runtime(
        config=_config(), sandbox=sandbox, mode=PermissionMode.PLAN, provider=_Broken()
    )
    result = await runtime.run("hi")
    assert result.status is RunStatus.ERROR
    assert "model call failed" in result.final_text


# -- sub-agents ---------------------------------------------------------------


def test_subagents_are_not_offered_tools_that_cannot_work(sandbox: Sandbox) -> None:
    from jarvis_code.subagents import SubAgentOrchestrator

    orch = SubAgentOrchestrator(
        provider=ScriptedProvider([]), sandbox=sandbox, parent_mode=PermissionMode.PLAN
    )
    names = orch._build_subruntime("explorer").executor.registry.names()
    assert "spawn_subagent" not in names and "ask_user" not in names


def test_a_crashing_subagent_is_reported_not_raised(sandbox: Sandbox) -> None:
    from jarvis_code.subagents import SubAgentOrchestrator

    class _Boom:
        name = "boom"
        model = "test"

        def complete(self, messages, tools=None):
            raise SystemError("kaboom")

    orch = SubAgentOrchestrator(provider=_Boom(), sandbox=sandbox, parent_mode=PermissionMode.PLAN)
    summary = orch.spawn("explorer", "look around")
    assert summary.startswith("[explorer]")  # an error summary, not an exception


# -- approval prompt ---------------------------------------------------------


def test_approval_prompt_shows_escape_sequences_instead_of_running_them() -> None:
    from jarvis_code.permissions import Capability, PermissionRequest, render_request

    # ESC[2K + CR would erase the line in a terminal, hiding `rm -rf build` so
    # only the harmless-looking tail is visible when the user approves.
    sneaky = "rm -rf build\x1b[2K\recho hello"
    block = render_request(
        PermissionRequest(
            tool_name="run_shell", capability=Capability.SHELL, summary="x", command=sneaky
        )
    )
    assert "\x1b" not in block and "\r" not in block  # nothing for the terminal to obey
    assert "rm -rf build" in block and "\\x1b[2K" in block  # shown literally


def test_typed_approval_fails_closed_when_stdin_is_closed() -> None:
    from jarvis_code.permissions import (
        Capability,
        Choice,
        PermissionRequest,
        TerminalPrompter,
    )

    def no_input(_prompt: str) -> str:
        raise EOFError

    prompter = TerminalPrompter(input_fn=no_input, output_fn=lambda _s: None)
    resp = prompter.request_permission(
        PermissionRequest(tool_name="run_shell", capability=Capability.SHELL, summary="x")
    )
    assert resp.choice is Choice.NO  # used to crash with an EOFError traceback


async def test_endless_truncation_gives_up_instead_of_looping(sandbox: Sandbox) -> None:
    cut_off = ModelResponse(
        tool_calls=[ToolCall("c", "write_file", {"path": "big.txt"})], stop_reason="length"
    )
    provider = ScriptedProvider([cut_off] * 10)
    runtime = assemble_runtime(
        config=_config(), sandbox=sandbox, mode=PermissionMode.DANGEROUS, provider=provider
    )
    result = await runtime.run("write a big file")
    assert result.status is RunStatus.ERROR
    assert len(provider.calls) == 3  # bounded, even though steps are unlimited
    assert "output-token limit" in result.final_text


# -- providers ----------------------------------------------------------------


def _capture_client(captured: list, response_json: dict):
    import httpx

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=response_json)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _provider_config(name: str, **extra) -> JarvisConfig:
    return JarvisConfig.model_validate(
        {
            "default_provider": name,
            "providers": {name: {"api_key": "sk-test-000000", "model": "m", **extra}},
        }
    )


def _parallel_tool_conversation():
    from jarvis_code.models import Message

    return [
        Message.user("go"),
        Message.assistant("", [ToolCall("call_0", "list_files", {}), ToolCall("call_1", "git_status", {})]),
        Message.tool_result("call_0", "list_files", "a.txt"),
        Message.tool_result("call_1", "git_status", ""),
    ]


def test_gemini_answers_parallel_calls_in_one_turn() -> None:
    from jarvis_code.models.gemini_style import _messages_to_gemini

    _, contents = _messages_to_gemini(_parallel_tool_conversation())
    # One model turn with 2 functionCalls must be answered by ONE user turn with
    # 2 functionResponses (a turn per result is rejected by the API).
    assert [(c["role"], len(c["parts"])) for c in contents] == [("user", 1), ("model", 2), ("user", 2)]


def test_gemini_omits_parameters_for_no_argument_tools() -> None:
    from jarvis_code.models.gemini_style import _tools_to_gemini

    decls = _tools_to_gemini(build_default_registry().specs())[0]["functionDeclarations"]
    by_name = {d["name"]: d for d in decls}
    assert "parameters" not in by_name["git_status"]  # an empty OBJECT schema is a 400
    assert "parameters" in by_name["read_file"]


def test_gemini_round_trips_the_thought_signature() -> None:
    import json

    from jarvis_code.models import Message, build_provider
    from jarvis_code.models.gemini_style import _messages_to_gemini

    captured: list = []
    reply = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "hidden reasoning", "thought": True},
                        {"functionCall": {"name": "list_files", "args": {}}, "thoughtSignature": "SIG"},
                    ]
                },
                "finishReason": "STOP",
            }
        ]
    }
    provider = build_provider(_provider_config("gemini"), http_client=_capture_client(captured, reply))
    response = provider.complete([Message.user("hi")])
    assert response.text == ""  # thought parts are not the reply
    call = response.tool_calls[0]
    _, contents = _messages_to_gemini([Message.assistant("", [call])])
    assert contents[0]["parts"][0]["thoughtSignature"] == "SIG"
    assert "generationConfig" not in json.loads(captured[0].content)


def test_anthropic_groups_tool_results_and_skips_empty_turns() -> None:
    from jarvis_code.models import Message
    from jarvis_code.models.anthropic import _messages_to_anthropic

    convo = [*_parallel_tool_conversation(), Message.assistant(""), Message.user("next")]
    _, msgs = _messages_to_anthropic(convo)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "user"]
    results = msgs[2]["content"]
    assert [b["tool_use_id"] for b in results] == ["call_0", "call_1"]
    assert results[1]["content"] == "(no output)"  # never an empty content block
    assert all(m["content"] != "" for m in msgs)  # the empty assistant turn is gone


@pytest.mark.parametrize(
    "name,extra,field",
    [
        ("openai", {}, "max_completion_tokens"),  # reasoning models 400 on max_tokens
        ("openai", {"endpoint": "http://localhost:11434/v1"}, "max_tokens"),
        ("deepseek", {}, "max_tokens"),
    ],
)
def test_openai_style_budget_parameter(name: str, extra: dict, field: str) -> None:
    import json

    from jarvis_code.models import Message, build_provider

    captured: list = []
    reply = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}
    provider = build_provider(
        _provider_config(name, **extra), http_client=_capture_client(captured, reply)
    )
    provider.complete([Message.user("hi")])
    body = json.loads(captured[0].content)
    assert field in body
    assert ("max_tokens" in body) != ("max_completion_tokens" in body)


def test_non_json_response_is_a_provider_error() -> None:
    import httpx

    from jarvis_code.models import Message, ProviderError, build_provider

    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>gateway</html>"))
    )
    provider = build_provider(_provider_config("anthropic"), http_client=client)
    with pytest.raises(ProviderError):
        provider.complete([Message.user("hi")])


def test_tool_arguments_that_are_not_an_object_become_empty() -> None:
    from jarvis_code.models import Message, build_provider

    reply = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"id": "x", "function": {"name": "read_file", "arguments": "[1, 2]"}}
                    ]
                }
            }
        ]
    }
    provider = build_provider(_provider_config("deepseek"), http_client=_capture_client([], reply))
    assert provider.complete([Message.user("hi")]).tool_calls[0].arguments == {}


def test_azure_without_an_endpoint_is_a_clear_config_error() -> None:
    from jarvis_code.config import ConfigError
    from jarvis_code.models import build_provider

    with pytest.raises(ConfigError, match="endpoint"):
        build_provider(_provider_config("azure"))


# -- CLI wiring ---------------------------------------------------------------


def test_provider_and_model_flags_reach_the_provider(sandbox: Sandbox) -> None:
    # --provider/--model were parsed and then silently ignored.
    config = JarvisConfig.model_validate(
        {
            "default_provider": "anthropic",
            "providers": {
                "anthropic": {"api_key": "sk-test-000000", "model": "claude-x"},
                "deepseek": {"api_key": "sk-test-111111", "model": "deepseek-chat"},
            },
        }
    )
    runtime = assemble_runtime(
        config=config, sandbox=sandbox, mode=PermissionMode.PLAN,
        provider_name="deepseek", model="deepseek-reasoner",
    )
    assert runtime.provider.name == "deepseek"
    assert runtime.provider.model == "deepseek-reasoner"


def test_short_path_only_abbreviates_whole_home_components() -> None:
    from jarvis_code.cli.chat import _short_path

    home = Path.home()
    assert _short_path(home) == "~"
    assert _short_path(home / "proj") == "~/proj"
    sibling = Path(str(home) + "2") / "x"  # /home/alice2/x is NOT under /home/alice
    assert _short_path(sibling) == str(sibling)


def test_unknown_provider_flag_is_a_friendly_error(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from jarvis_code.cli.main import app

    (tmp_path / ".jarvis.toml").write_text(
        'default_provider = "anthropic"\n[providers.anthropic]\n'
        'api_key = "sk-test-000000"\nmodel = "m"\n[voice]\nenabled = false\n'
    )
    result = CliRunner().invoke(
        app, ["--sandbox", str(tmp_path), "--provider", "nope", "run", "hello"]
    )
    assert result.exit_code == 1
    assert "not configured" in result.output
    assert "Traceback" not in result.output


# -- secrets / files ----------------------------------------------------------


def test_env_scrub_catches_bare_and_suffixless_credential_names() -> None:
    from jarvis_code.utils.logging import scrub_secret_env

    env = {
        "API_KEY": "a", "TOKEN": "b", "PGPASSWORD": "c", "SECRET": "d", "GH_PAT": "e",
        "OPENAI_API_KEY": "f", "AWS_SECRET_ACCESS_KEY": "g", "MYSQL_PWD": "h",
        "PATH": "/bin", "HOME": "/home/x", "EDITOR": "vi", "TOKENIZERS_PARALLELISM": "false",
    }
    assert sorted(scrub_secret_env(env)) == ["EDITOR", "HOME", "PATH", "TOKENIZERS_PARALLELISM"]


def test_init_writes_an_owner_only_config(tmp_path: Path) -> None:
    import os
    import stat

    from typer.testing import CliRunner

    from jarvis_code.cli.main import app

    assert CliRunner().invoke(app, ["--sandbox", str(tmp_path), "init"]).exit_code == 0
    mode = stat.S_IMODE(os.stat(tmp_path / ".jarvis" / "config.toml").st_mode)
    assert mode == 0o600  # it is about to hold an API key


def test_transcript_never_escapes_or_crashes_when_dot_jarvis_is_a_symlink(
    tmp_path: Path, tmp_path_factory
) -> None:
    from jarvis_code.sessions import SessionWriter

    outside = tmp_path_factory.mktemp("elsewhere")
    (tmp_path / ".jarvis").symlink_to(outside)
    SessionWriter(Sandbox(tmp_path)).prompt("hello")  # used to raise SandboxViolation
    assert not any(outside.rglob("*.jsonl"))  # and nothing was written outside


# -- jarvis's own config is off-limits to the agent ---------------------------


@pytest.fixture
def configured(root: Path) -> Path:
    (root / ".jarvis").mkdir()
    (root / ".jarvis" / "config.toml").write_text(
        'default_provider = "anthropic"\n[providers.anthropic]\n'
        'api_key = "sk-loaded-key-000000"\nmodel = "m"\n'
    )
    # A SECOND config nobody loaded: its key is not registered with the redactor.
    (root / ".jarvis.toml").write_text(
        '[providers.openai]\napi_key = "sk-unregistered-key-999999"\nmodel = "m"\n'
    )
    return root


def test_agent_cannot_read_config_files(executor, configured: Path) -> None:
    for path in (".jarvis/config.toml", ".jarvis.toml", "./.jarvis/../.jarvis.toml"):
        res = executor.execute("read_file", {"path": path}).result
        assert not res.ok and "off-limits" in res.error
        assert "sk-" not in (res.output + res.error)


def test_agent_cannot_read_config_through_a_symlink(executor, configured: Path) -> None:
    (configured / "innocent.txt").symlink_to(configured / ".jarvis.toml")
    res = executor.execute("read_file", {"path": "innocent.txt"}).result
    assert not res.ok and "sk-" not in (res.output + res.error)


def test_search_never_surfaces_config_contents(executor, configured: Path) -> None:
    grep = executor.execute("grep_text", {"pattern": "api_key"}).result.output
    assert "sk-" not in grep and ".jarvis" not in grep
    assert ".jarvis.toml" not in executor.execute("search_files", {"pattern": "*.toml"}).result.output


@pytest.mark.parametrize(
    "tool,args",
    [
        ("write_file", {"path": ".jarvis/config.toml", "content": "endpoint = 'http://evil'"}),
        ("write_file", {"path": ".jarvis.toml", "content": "x"}),
        ("edit_file", {"path": ".jarvis/config.toml", "old_string": "m", "new_string": "n"}),
        ("delete_file", {"path": ".jarvis/config.toml"}),
        ("delete_file", {"path": ".jarvis"}),
        ("move_file", {"src": ".jarvis", "dst": "elsewhere"}),
        ("move_file", {"src": "a.txt", "dst": ".jarvis/config.toml"}),
    ],
)
def test_agent_cannot_modify_config_even_in_dangerous_mode(
    executor, configured: Path, tool: str, args: dict
) -> None:
    before = (configured / ".jarvis" / "config.toml").read_text()
    res = executor.execute(tool, args).result
    assert not res.ok and "off-limits" in res.error
    assert (configured / ".jarvis" / "config.toml").read_text() == before


def test_agent_cannot_plant_a_config_where_none_exists(executor, root: Path) -> None:
    # A planted config could point `endpoint` at another host and receive the
    # user's env-provided key on the next run.
    res = executor.execute("write_file", {"path": ".jarvis.toml", "content": "x"}).result
    assert not res.ok
    assert not (root / ".jarvis.toml").exists()


def test_session_transcripts_under_dot_jarvis_stay_usable(executor, configured: Path) -> None:
    # Only the config is protected — not everything under .jarvis/.
    (configured / ".jarvis" / "sessions").mkdir()
    (configured / ".jarvis" / "sessions" / "s.jsonl").write_text("{}\n")
    assert executor.execute("read_file", {"path": ".jarvis/sessions/s.jsonl"}).result.ok


# -- voice --------------------------------------------------------------------


class _RecordingEngine:
    name = "rec"

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def speak(self, text, options=None) -> None:
        self.spoken.append(text)

    async def stop(self) -> None: ...

    async def is_available(self) -> bool:
        return True


async def test_voice_speaks_while_the_agent_loop_is_blocked() -> None:
    import time

    from jarvis_code.config import VoiceConfig
    from jarvis_code.voice import VoiceSubsystem

    engine = _RecordingEngine()
    vs = VoiceSubsystem(engine, VoiceConfig())
    await vs.start()
    try:
        vs.say("Awaiting your authorization.")
        # Block the event loop the way a synchronous tool / approval prompt does.
        deadline = time.monotonic() + 3
        while not engine.spoken and time.monotonic() < deadline:
            time.sleep(0.02)  # deliberately NOT awaiting
        assert engine.spoken == ["Awaiting your authorization."]
    finally:
        await vs.aclose()


async def test_approval_request_is_announced_by_voice(sandbox: Sandbox) -> None:
    from jarvis_code.config import VoiceConfig
    from jarvis_code.permissions import DenyPrompter
    from jarvis_code.voice import VoiceReporter, VoiceSubsystem

    engine = _RecordingEngine()
    vs = VoiceSubsystem(engine, VoiceConfig())
    await vs.start()
    provider = ScriptedProvider(
        [
            ModelResponse(tool_calls=[ToolCall("c1", "run_shell", {"command": "pytest -q --secret=x"})]),
            ModelResponse(text="ok"),
        ]
    )
    runtime = assemble_runtime(
        config=_config(), sandbox=sandbox, mode=PermissionMode.AUTO, provider=provider,
        prompter=DenyPrompter(), reporter=VoiceReporter(vs),
    )
    await runtime.run("run tests")
    await vs.drain()
    await vs.aclose()
    assert "Awaiting your authorization to run pytest, sir." in engine.spoken
    assert not any("secret" in line for line in engine.spoken)  # program name only


# -- provider resilience -------------------------------------------------------


def _flaky_client(statuses: list[int], seen: list):
    import httpx

    queue = list(statuses)

    def handler(request):
        seen.append(request)
        status = queue.pop(0)
        if status == 200:
            return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})
        return httpx.Response(status, json={"type": "error", "error": {"message": "overloaded"}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_transient_api_errors_are_retried() -> None:
    from jarvis_code.models import Message, build_provider

    seen: list = []
    provider = build_provider(_provider_config("anthropic"), http_client=_flaky_client([529, 429, 200], seen))
    provider._sleep = lambda _s: None  # no real backoff in tests
    assert provider.complete([Message.user("hi")]).text == "ok"
    assert len(seen) == 3  # one overloaded reply used to end the turn


def test_permanent_api_errors_are_not_retried_and_read_cleanly() -> None:
    from jarvis_code.models import Message, ProviderError, build_provider

    seen: list = []
    provider = build_provider(_provider_config("anthropic"), http_client=_flaky_client([401], seen))
    provider._sleep = lambda _s: None
    with pytest.raises(ProviderError) as exc:
        provider.complete([Message.user("hi")])
    assert len(seen) == 1
    text = str(exc.value)
    assert "401" in text and "overloaded" in text  # the message, not a JSON blob
    assert "{" not in text and "API key was rejected" in text


def test_retries_give_up_eventually() -> None:
    from jarvis_code.models import Message, ProviderError, build_provider

    seen: list = []
    provider = build_provider(_provider_config("anthropic"), http_client=_flaky_client([503] * 9, seen))
    provider._sleep = lambda _s: None
    with pytest.raises(ProviderError):
        provider.complete([Message.user("hi")])
    assert len(seen) == 4
