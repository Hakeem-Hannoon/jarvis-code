"""Phase 10 tests: sub-agent permission inheritance, isolation, summary-only."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_code.models import ModelResponse, ToolCall
from jarvis_code.permissions import PermissionMode
from jarvis_code.sandbox import Sandbox
from jarvis_code.sessions import SessionWriter
from jarvis_code.subagents import SubAgentOrchestrator, clamp_mode
from jarvis_code.tools import ToolContext
from jarvis_code.tools.subagent_tool import SpawnSubagentTool
from jarvis_code.agent.state import RunState

PLAN, AUTO, DANGEROUS = (
    PermissionMode.PLAN,
    PermissionMode.AUTO,
    PermissionMode.DANGEROUS,
)


class FakeProvider:
    name = "fake"
    model = "m"

    def __init__(self, responses) -> None:
        self.responses = list(responses)

    def complete(self, messages, tools=None):
        return self.responses.pop(0) if self.responses else ModelResponse(text="end")


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "mod.py").write_text("MARKER_INTERNAL_DETAIL = 1\n")
    return Sandbox(root)


# -- permission clamping (never exceed parent) ------------------------------


def test_clamp_never_exceeds_parent() -> None:
    assert clamp_mode(PLAN, DANGEROUS) == PLAN
    assert clamp_mode(PLAN, AUTO) == PLAN
    assert clamp_mode(AUTO, DANGEROUS) == AUTO
    assert clamp_mode(DANGEROUS, PLAN) == PLAN  # downgrade allowed
    assert clamp_mode(AUTO) == AUTO  # inherit
    assert clamp_mode(DANGEROUS) == DANGEROUS


def test_effective_mode_clamped() -> None:
    orch = SubAgentOrchestrator(provider=FakeProvider([]), sandbox=None, parent_mode=AUTO)
    assert orch.effective_mode() == AUTO
    assert orch.effective_mode(DANGEROUS) == AUTO  # cannot escalate


# -- sub-agent cannot exceed parent's permissions (behavioral) --------------


def test_plan_parent_subagent_cannot_write(sandbox: Sandbox) -> None:
    provider = FakeProvider(
        [
            ModelResponse(tool_calls=[ToolCall("s1", "write_file", {"path": "sub.txt", "content": "x"})]),
            ModelResponse(text="Could not write in plan mode."),
        ]
    )
    orch = SubAgentOrchestrator(provider=provider, sandbox=sandbox, parent_mode=PLAN)
    summary = orch.spawn("implementer", "create sub.txt")
    assert not (Path(sandbox.root) / "sub.txt").exists()  # write was denied
    assert summary.startswith("[implementer]")


# -- sub-agent confined to the same sandbox ---------------------------------


def test_subagent_confined_to_sandbox_even_in_dangerous(sandbox: Sandbox) -> None:
    outside = Path(sandbox.root).parent / "escape.txt"
    provider = FakeProvider(
        [
            ModelResponse(tool_calls=[ToolCall("s1", "write_file", {"path": "../escape.txt", "content": "x"})]),
            ModelResponse(text="Could not escape the sandbox."),
        ]
    )
    # Even with the most permissive parent mode, the sandbox still confines.
    orch = SubAgentOrchestrator(provider=provider, sandbox=sandbox, parent_mode=DANGEROUS)
    orch.spawn("implementer", "write outside the root")
    assert not outside.exists()


def test_subagent_uses_same_sandbox_root(sandbox: Sandbox) -> None:
    orch = SubAgentOrchestrator(provider=FakeProvider([]), sandbox=sandbox, parent_mode=PLAN)
    sub = orch._build_subruntime("explorer")
    assert sub.executor.ctx.sandbox is sandbox
    # And it cannot spawn further sub-agents.
    assert sub.executor.ctx.orchestrator is None


# -- parent receives a summary, not the transcript --------------------------


def test_parent_receives_summary_not_transcript(sandbox: Sandbox) -> None:
    parent_session = SessionWriter(sandbox, session_id="parent")
    parent_state = RunState()
    provider = FakeProvider(
        [
            ModelResponse(tool_calls=[ToolCall("s1", "read_file", {"path": "mod.py"})]),
            ModelResponse(text="Summary: the module defines one constant."),
        ]
    )
    orch = SubAgentOrchestrator(
        provider=provider,
        sandbox=sandbox,
        parent_mode=PLAN,
        parent_session=parent_session,
        parent_state=parent_state,
    )
    summary = orch.spawn("explorer", "describe mod.py")

    # The concise summary contains the sub-agent's conclusion ...
    assert "Summary: the module defines one constant." in summary
    # ... but NOT the intermediate tool output (the file contents).
    assert "MARKER_INTERNAL_DETAIL" not in summary
    # Parent state/session record only the summary.
    assert any("explorer" in s for s in parent_state.subagent_summaries)
    assert all("MARKER_INTERNAL_DETAIL" not in s for s in parent_state.subagent_summaries)


def test_unknown_agent_type_returns_message(sandbox: Sandbox) -> None:
    orch = SubAgentOrchestrator(provider=FakeProvider([]), sandbox=sandbox, parent_mode=PLAN)
    assert "Unknown sub-agent type" in orch.spawn("wizard", "do magic")


# -- the spawn_subagent tool delegates to the orchestrator ------------------


def test_spawn_tool_delegates_to_orchestrator(sandbox: Sandbox) -> None:
    class FakeOrch:
        def spawn(self, agent_type, task):
            return f"[{agent_type}] handled: {task}"

    tool = SpawnSubagentTool()
    ctx = ToolContext(sandbox=sandbox, orchestrator=FakeOrch())
    res = tool.run(ctx, {"agent_type": "explorer", "task": "map the repo"})
    assert res.ok
    assert "handled: map the repo" in res.output
