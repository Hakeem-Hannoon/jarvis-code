"""Phase 9 tests: events, instruction queue + classification, steering, interrupts."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_code.agent import assemble_runtime
from jarvis_code.agent.loop import RunStatus
from jarvis_code.config import JarvisConfig, MicMode, VoiceInputConfig
from jarvis_code.events import (
    EventBus,
    EventType,
    InstructionKind,
    InstructionQueue,
    KeyAction,
    QueueAwarePrompter,
    action_for_key,
    classify,
    describe_controls,
)
from jarvis_code.input import MicController, NoopSTT
from jarvis_code.models import ModelResponse, ToolCall
from jarvis_code.permissions import (
    Capability,
    Choice,
    DenyPrompter,
    PermissionMode,
    PermissionRequest,
)
from jarvis_code.sandbox import Sandbox
from jarvis_code.sessions import SessionWriter


# -- classification ---------------------------------------------------------


@pytest.mark.parametrize(
    "text,kind",
    [
        ("stop", InstructionKind.HARD_INTERRUPT),
        ("stop everything now", InstructionKind.HARD_INTERRUPT),
        ("abort", InstructionKind.HARD_INTERRUPT),
        ("wait", InstructionKind.SOFT_INTERRUPT),
        ("hold on a sec", InstructionKind.SOFT_INTERRUPT),
        ("yes", InstructionKind.PERMISSION_RESPONSE),
        ("no", InstructionKind.PERMISSION_RESPONSE),
        ("why did you do that?", InstructionKind.QUESTION),
        ("what files changed", InstructionKind.QUESTION),
        ("also add type hints to the parser", InstructionKind.STEERING),
    ],
)
def test_classify(text: str, kind: InstructionKind) -> None:
    assert classify(text) == kind


# -- instruction queue ------------------------------------------------------


def test_queue_preserves_order() -> None:
    q = InstructionQueue()
    q.submit("first thing", kind=InstructionKind.STEERING)
    q.submit("second thing", kind=InstructionKind.STEERING)
    drained = q.drain()
    assert [i.text for i in drained] == ["first thing", "second thing"]
    assert q.drain() == []  # emptied


def test_queue_emits_events() -> None:
    bus = EventBus()
    q = InstructionQueue(bus)
    q.submit("refactor the loop")  # steering
    q.submit("stop")  # hard interrupt
    types = [e.type for e in bus.history]
    assert EventType.STEERING_INSTRUCTION_RECEIVED in types
    assert EventType.INTERRUPT_REQUESTED in types


# -- voice/text as permission response (same 1/2/3 menu) --------------------


def _perm_req() -> PermissionRequest:
    return PermissionRequest(tool_name="run_shell", capability=Capability.SHELL, summary="x")


def test_queue_aware_prompter_yes() -> None:
    q = InstructionQueue()
    q.submit("yes")
    assert QueueAwarePrompter(q).request_permission(_perm_req()).choice == Choice.YES


def test_queue_aware_prompter_no() -> None:
    q = InstructionQueue()
    q.submit("no")
    assert QueueAwarePrompter(q).request_permission(_perm_req()).choice == Choice.NO


def test_queue_aware_prompter_else_collects_feedback() -> None:
    q = InstructionQueue()
    q.submit("run the linter instead")  # an arbitrary spoken instruction
    resp = QueueAwarePrompter(q).request_permission(_perm_req())
    assert resp.choice == Choice.SOMETHING_ELSE
    assert resp.feedback == "run the linter instead"


def test_queue_aware_prompter_defers_to_inner_when_empty() -> None:
    q = InstructionQueue()
    prompter = QueueAwarePrompter(q, inner=DenyPrompter())
    assert prompter.request_permission(_perm_req()).choice == Choice.NO


# -- mic-mode-change events -------------------------------------------------


def test_mic_mode_change_publishes_event() -> None:
    bus = EventBus()
    mc = MicController(
        VoiceInputConfig(enabled=True, mode=MicMode.OPEN_MIC),
        NoopSTT(),
        on_mode_change=lambda m: bus.emit(EventType.VOICE_MODE_CHANGED, mode=m.value),
    )
    mc.cycle_mode()
    mc.cycle_mode()
    changes = [e for e in bus.history if e.type == EventType.VOICE_MODE_CHANGED]
    assert [e.data["mode"] for e in changes] == ["push_to_talk", "muted"]


# -- keyboard controls ------------------------------------------------------


def test_key_bindings() -> None:
    assert action_for_key("m") == KeyAction.CYCLE_MIC
    assert action_for_key("V") == KeyAction.TOGGLE_PTT
    assert action_for_key("space") == KeyAction.HOLD_TO_TALK
    assert action_for_key("esc") == KeyAction.SOFT_INTERRUPT
    assert action_for_key("ctrl+c") == KeyAction.HARD_STOP
    assert action_for_key("?") == KeyAction.SHOW_CONTROLS
    assert action_for_key("zzz") == KeyAction.NONE


def test_describe_controls_lists_keys() -> None:
    text = describe_controls()
    for token in ("M", "V", "Space", "Esc", "Ctrl+C", "?"):
        assert token in text


# -- loop integration: steering + interrupts --------------------------------


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    (tmp_path / "app.py").write_text("x = 1\n")
    return Sandbox(tmp_path)


def _config() -> JarvisConfig:
    return JarvisConfig.model_validate(
        {"default_provider": "anthropic", "providers": {"anthropic": {"api_key": "k" * 8, "model": "m"}}}
    )


class InjectingProvider:
    """Records the messages seen each call and can inject a live instruction."""

    name = "inject"
    model = "m"

    def __init__(self, instructions: InstructionQueue, responses, inject=None) -> None:
        self.instructions = instructions
        self.responses = list(responses)
        self.inject = inject  # (call_number, text, kind) or None
        self.seen: list[list[str]] = []
        self.calls = 0

    def complete(self, messages, tools=None):
        self.seen.append([m.content for m in messages])
        self.calls += 1
        if self.inject and self.calls == self.inject[0]:
            self.instructions.submit(self.inject[1], kind=self.inject[2])
        return self.responses.pop(0) if self.responses else ModelResponse(text="done")


@pytest.mark.asyncio
async def test_steering_alters_the_next_step(sandbox: Sandbox) -> None:
    q = InstructionQueue()
    provider = InjectingProvider(
        q,
        responses=[
            ModelResponse(tool_calls=[ToolCall("c1", "read_file", {"path": "app.py"})]),
            ModelResponse(text="Done, focused as asked."),
        ],
        inject=(1, "focus on the parser module", InstructionKind.STEERING),
    )
    runtime = assemble_runtime(
        config=_config(),
        sandbox=sandbox,
        mode=PermissionMode.PLAN,
        provider=provider,
        instructions=q,
        session=SessionWriter(sandbox, session_id="steer"),
    )
    result = await runtime.run("Explain the project.")
    assert result.status == RunStatus.COMPLETE
    # The steering injected during step 1 must appear in the context the model
    # sees on step 2 — i.e. it measurably altered the next step.
    second_call_context = " ".join(provider.seen[1])
    assert "focus on the parser module" in second_call_context


@pytest.mark.asyncio
async def test_hard_interrupt_aborts_run(sandbox: Sandbox) -> None:
    q = InstructionQueue()
    provider = InjectingProvider(
        q,
        responses=[
            ModelResponse(tool_calls=[ToolCall("c1", "read_file", {"path": "app.py"})]),
            ModelResponse(text="should not get here"),
        ],
        inject=(1, "stop", InstructionKind.HARD_INTERRUPT),
    )
    runtime = assemble_runtime(
        config=_config(),
        sandbox=sandbox,
        mode=PermissionMode.PLAN,
        provider=provider,
        instructions=q,
        session=SessionWriter(sandbox, session_id="abort"),
    )
    result = await runtime.run("Explain the project.")
    assert result.status == RunStatus.ABORTED
    assert provider.calls == 1  # never reached step 2


@pytest.mark.asyncio
async def test_voice_permission_response_drives_run(sandbox: Sandbox) -> None:
    # In auto mode a write needs approval; a queued "yes" approves it via the
    # same menu (no parallel permission path).
    q = InstructionQueue()
    q.submit("yes")
    provider = InjectingProvider(
        q,
        responses=[
            ModelResponse(tool_calls=[ToolCall("c1", "write_file", {"path": "new.txt", "content": "hi"})]),
            ModelResponse(text="Wrote the file."),
        ],
    )
    runtime = assemble_runtime(
        config=_config(),
        sandbox=sandbox,
        mode=PermissionMode.AUTO,
        provider=provider,
        prompter=QueueAwarePrompter(q),
        instructions=q,
        session=SessionWriter(sandbox, session_id="voiceperm"),
    )
    result = await runtime.run("Create new.txt")
    assert result.status == RunStatus.COMPLETE
    assert (Path(sandbox.root) / "new.txt").read_text() == "hi"
