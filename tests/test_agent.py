"""Phase 5 tests: runtime loop, sessions, project memory, compaction, redaction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_code.agent import (
    AgentRuntime,
    ConversationContext,
    RunState,
    RunStatus,
    assemble_runtime,
    build_system_prompt,
    load_project_memory,
)
from jarvis_code.config import JarvisConfig
from jarvis_code.models import ModelResponse, ToolCall
from jarvis_code.permissions import (
    Choice,
    PermissionMode,
    PermissionResponse,
    ScriptedPrompter,
)
from jarvis_code.sandbox import Sandbox
from jarvis_code.sessions import SessionWriter
from jarvis_code.utils import logging as jlog


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.calls: list = []

    def complete(self, messages, tools=None):
        self.calls.append((messages, tools))
        if not self._responses:
            return ModelResponse(text="(no more scripted responses)")
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def _clean_secrets():
    jlog.clear_secrets()
    yield
    jlog.clear_secrets()


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Sandbox:
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "README.md").write_text("# fixture\n")
    return Sandbox(tmp_path)


def _config() -> JarvisConfig:
    return JarvisConfig.model_validate(
        {"default_provider": "anthropic", "providers": {"anthropic": {"api_key": "k" * 8, "model": "m"}}}
    )


def _read_transcript(sandbox: Sandbox, writer: SessionWriter) -> list[dict]:
    path = Path(sandbox.root) / writer.path
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# -- project memory ---------------------------------------------------------


def test_memory_loaded_from_jarvis_md(fixture_repo: Sandbox) -> None:
    (Path(fixture_repo.root) / "JARVIS.md").write_text("Always run black before committing.")
    assert load_project_memory(fixture_repo) == "Always run black before committing."


def test_memory_fallback_to_dot_jarvis(fixture_repo: Sandbox) -> None:
    (Path(fixture_repo.root) / ".jarvis").mkdir()
    (Path(fixture_repo.root) / ".jarvis" / "memory.md").write_text("Prefer pathlib.")
    assert load_project_memory(fixture_repo) == "Prefer pathlib."


def test_memory_absent_returns_none(fixture_repo: Sandbox) -> None:
    assert load_project_memory(fixture_repo) is None


def test_memory_is_deprioritized_in_system_prompt() -> None:
    prompt = build_system_prompt(
        mode=PermissionMode.PLAN,
        sandbox_root="/root",
        tool_specs=[{"name": "read_file", "description": "read"}],
        memory="Use 2-space indents.",
    )
    assert "Use 2-space indents." in prompt
    # Memory must be explicitly ranked below user instructions + safety.
    assert "ranks BELOW" in prompt
    # Safety rules appear before the memory section.
    assert prompt.index("SAFETY RULES") < prompt.index("PROJECT MEMORY")


# -- plan-mode end-to-end ---------------------------------------------------


@pytest.mark.asyncio
async def test_plan_mode_end_to_end_produces_plan_and_transcript(fixture_repo: Sandbox) -> None:
    provider = FakeProvider(
        [
            ModelResponse(tool_calls=[ToolCall("c1", "list_files", {"path": "."})]),
            ModelResponse(tool_calls=[ToolCall("c2", "read_file", {"path": "app.py"})]),
            ModelResponse(text="Plan:\n1. Add type hints to add().\n2. Add a test."),
        ]
    )
    session = SessionWriter(fixture_repo, session_id="test-plan")
    runtime = assemble_runtime(
        config=_config(),
        sandbox=fixture_repo,
        mode=PermissionMode.PLAN,
        provider=provider,
        session=session,
    )
    result = await runtime.run("Explain this project and propose improvements.")

    assert result.status == RunStatus.COMPLETE
    assert "Plan:" in result.final_text

    records = _read_transcript(fixture_repo, session)
    kinds = [r["type"] for r in records]
    assert kinds[0] == "prompt"
    assert "model_response" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "run_end"
    # the read tools actually ran (read-only is allowed in plan)
    assert any(r["type"] == "tool_result" and r["ok"] for r in records)


@pytest.mark.asyncio
async def test_plan_mode_denies_writes(fixture_repo: Sandbox) -> None:
    provider = FakeProvider(
        [
            ModelResponse(tool_calls=[ToolCall("c1", "write_file", {"path": "x.py", "content": "1"})]),
            ModelResponse(text="I cannot write in plan mode; here is the plan instead."),
        ]
    )
    session = SessionWriter(fixture_repo, session_id="test-deny")
    runtime = assemble_runtime(
        config=_config(),
        sandbox=fixture_repo,
        mode=PermissionMode.PLAN,
        provider=provider,
        session=session,
    )
    result = await runtime.run("Add a file.")
    assert result.status == RunStatus.COMPLETE
    assert not (Path(fixture_repo.root) / "x.py").exists()

    records = _read_transcript(fixture_repo, session)
    decisions = [r for r in records if r["type"] == "permission_decision"]
    assert decisions and decisions[0]["outcome"] == "denied"


@pytest.mark.asyncio
async def test_do_something_else_feeds_steering(fixture_repo: Sandbox) -> None:
    provider = FakeProvider(
        [
            ModelResponse(tool_calls=[ToolCall("c1", "run_shell", {"command": "flake8 ."})]),
            ModelResponse(text="Understood, switching to ruff in the plan."),
        ]
    )
    prompter = ScriptedPrompter(
        [PermissionResponse(choice=Choice.SOMETHING_ELSE, feedback="use ruff instead")]
    )
    session = SessionWriter(fixture_repo, session_id="test-revise")
    runtime = assemble_runtime(
        config=_config(),
        sandbox=fixture_repo,
        mode=PermissionMode.AUTO,
        provider=provider,
        prompter=prompter,
        session=session,
    )
    result = await runtime.run("Lint the project.")
    assert result.status == RunStatus.COMPLETE

    records = _read_transcript(fixture_repo, session)
    steering = [r for r in records if r["type"] == "steering"]
    assert steering and "ruff" in steering[0]["text"]


# -- secret redaction in transcripts ----------------------------------------


@pytest.mark.asyncio
async def test_no_secrets_in_transcript(fixture_repo: Sandbox) -> None:
    secret = "sk-supersecret-1234567890"
    jlog.register_secret(secret)
    # The model carelessly echoes the key in its text; it must be scrubbed.
    provider = FakeProvider([ModelResponse(text=f"The key is {secret}. Done.")])
    session = SessionWriter(fixture_repo, session_id="test-secret")
    runtime = assemble_runtime(
        config=_config(),
        sandbox=fixture_repo,
        mode=PermissionMode.PLAN,
        provider=provider,
        session=session,
    )
    await runtime.run("Say the key.")

    raw = (Path(fixture_repo.root) / session.path).read_text()
    assert secret not in raw
    assert "***REDACTED***" in raw


# -- compaction -------------------------------------------------------------


def test_compaction_preserves_state_and_recent() -> None:
    ctx = ConversationContext("SYSTEM", max_chars=200, keep_recent=2)
    for i in range(10):
        ctx.add_user("x" * 50 + f" msg{i}")
    state = RunState(goal="ship the feature", files_changed=["a.py"], commands_run=["pytest"])

    assert ctx.needs_compaction()
    assert ctx.compact(state) is True
    # summary + the two most recent messages
    assert len(ctx.messages) == 3
    assert "ship the feature" in ctx.messages[0].content
    assert "a.py" in ctx.messages[0].content
    assert "msg9" in ctx.messages[-1].content


def test_compaction_noop_when_small() -> None:
    ctx = ConversationContext("SYSTEM", max_chars=10_000)
    ctx.add_user("hello")
    assert ctx.compact(RunState(goal="g")) is False


# -- step limit: unlimited by default, still cappable -----------------------


class _AlwaysToolProvider:
    model = "loop"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools=None):
        self.calls += 1
        return ModelResponse(tool_calls=[ToolCall(f"c{self.calls}", "list_files", {"path": "."})])


@pytest.mark.asyncio
async def test_unlimited_steps_by_default_runs_past_old_cap(fixture_repo: Sandbox) -> None:
    # 25 tool-call rounds then a finish — more than the old default of 20.
    responses = [
        ModelResponse(tool_calls=[ToolCall(f"c{i}", "list_files", {"path": "."})])
        for i in range(25)
    ] + [ModelResponse(text="Done.")]
    runtime = assemble_runtime(
        config=_config(), sandbox=fixture_repo, mode=PermissionMode.PLAN,
        provider=FakeProvider(responses),
    )
    result = await runtime.run("look around a lot")
    assert result.status == RunStatus.COMPLETE
    assert result.steps == 26  # would have stopped at 20 under the old default


@pytest.mark.asyncio
async def test_max_steps_can_still_be_capped(fixture_repo: Sandbox) -> None:
    runtime = assemble_runtime(
        config=_config(), sandbox=fixture_repo, mode=PermissionMode.PLAN,
        provider=_AlwaysToolProvider(), max_steps=3,
    )
    result = await runtime.run("loop forever")
    assert result.status == RunStatus.MAX_STEPS
    assert result.steps == 3


# -- effort levels ----------------------------------------------------------


def test_effort_profiles() -> None:
    from jarvis_code.agent.effort import profile_for
    from jarvis_code.config import Effort

    # Sub-agents only at madmax.
    assert profile_for(Effort.MADMAX).use_subagents is True
    assert all(not profile_for(e).use_subagents for e in (Effort.LOW, Effort.MEDIUM, Effort.HIGH))
    # low caps steps; medium (the default) keeps them unlimited.
    assert profile_for(Effort.LOW).max_steps == 15
    assert profile_for(Effort.MEDIUM).max_steps is None


def test_build_system_prompt_includes_effort_note() -> None:
    from jarvis_code.agent.prompts import build_system_prompt

    prompt = build_system_prompt(
        mode=PermissionMode.AUTO, sandbox_root="/x", tool_specs=[], memory=None,
        effort_note="EFFORT high: be thorough.",
    )
    assert "EFFORT high: be thorough." in prompt


@pytest.mark.asyncio
async def test_madmax_exposes_subagents_and_unlimited_steps(fixture_repo: Sandbox) -> None:
    from jarvis_code.config import Effort

    rt = assemble_runtime(
        config=_config(), sandbox=fixture_repo, mode=PermissionMode.PLAN,
        provider=FakeProvider([ModelResponse(text="ok")]), effort=Effort.MADMAX,
    )
    assert "spawn_subagent" in rt.executor.registry
    assert rt.max_steps is None  # unlimited


@pytest.mark.asyncio
async def test_low_effort_caps_steps_and_hides_subagents(fixture_repo: Sandbox) -> None:
    from jarvis_code.config import Effort

    rt = assemble_runtime(
        config=_config(), sandbox=fixture_repo, mode=PermissionMode.PLAN,
        provider=FakeProvider([ModelResponse(text="ok")]), effort=Effort.LOW,
    )
    assert "spawn_subagent" not in rt.executor.registry
    assert rt.max_steps == 15
