"""The agent runtime loop.

Per step: drain steering -> compact if needed -> call model -> record + add
assistant turn -> if no tool calls, finish -> else route each tool call through
the permission manager, execute allowed ones, append results, and fold any
"Do Something Else" feedback back in as steering. Repeat until complete, blocked,
or the step limit.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass
from enum import Enum

from jarvis_code.config import Effort, JarvisConfig
from jarvis_code.events import EventBus, EventType, InstructionKind, InstructionQueue
from jarvis_code.models import ProviderError, build_provider
from jarvis_code.permissions import OutcomeKind, PermissionManager, PermissionMode
from jarvis_code.sandbox import Sandbox
from jarvis_code.sessions import SessionWriter
from jarvis_code.tools import ToolContext, ToolExecutor, build_default_registry
from jarvis_code.utils.logging import get_logger

from .context import ConversationContext
from .effort import profile_for
from .memory import load_project_memory
from .prompts import build_system_prompt
from .reporter import NoopReporter, Reporter
from .state import RunState

_log = get_logger("agent")


def _arg_summary(args: dict) -> str:
    """A short one-line summary of tool arguments for step display."""
    for key in ("path", "command", "pattern", "src"):
        if key in args:
            return str(args[key])[:80]
    return ""


class RunStatus(str, Enum):
    COMPLETE = "complete"
    MAX_STEPS = "max_steps"
    ERROR = "error"
    ABORTED = "aborted"


@dataclass
class RunResult:
    status: RunStatus
    final_text: str
    steps: int
    state: RunState


class AgentRuntime:
    def __init__(
        self,
        *,
        provider,
        executor: ToolExecutor,
        context: ConversationContext,
        session: SessionWriter,
        state: RunState,
        max_steps: int | None = None,
        reporter: Reporter | None = None,
        instructions: InstructionQueue | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.provider = provider
        self.executor = executor
        self.context = context
        self.session = session
        self.state = state
        self.max_steps = max_steps
        self.reporter: Reporter = reporter or NoopReporter()
        self.bus = bus if bus is not None else EventBus()
        self.instructions = instructions if instructions is not None else InstructionQueue(self.bus)
        self._aborted = False  # set by a hard interrupt
        self._abort_reason = ""

    def steer(self, instruction: str) -> None:
        """Queue a steering instruction to fold in at the next safe checkpoint."""
        self.instructions.submit(instruction)

    def _checkpoint(self, name: str) -> bool:
        """Drain + apply live instructions. Returns False if the run must abort.

        Safe checkpoints (the loop calls this before the model call, before each
        tool execution, after each tool result, and before finalizing).
        """
        for instr in self.instructions.drain_steering():
            if instr.kind == InstructionKind.HARD_INTERRUPT:
                self._aborted = True
                self._abort_reason = instr.text
                self.bus.emit(EventType.INTERRUPT_REQUESTED, kind="hard", at=name)
            elif instr.kind == InstructionKind.SOFT_INTERRUPT:
                self.context.add_user(f"[soft-interrupt] After the current step, adjust: {instr.text}")
                self.session.steering(f"soft-interrupt: {instr.text}")
            elif instr.kind == InstructionKind.QUESTION:
                self.context.add_user(f"[user question — answer briefly, keep working] {instr.text}")
            else:
                # STEERING or a permission response arriving with no active prompt.
                self.context.add_user(f"[steering] {instr.text}")
                self.session.steering(instr.text)
        return not self._aborted

    def _aborted_result(self, step: int) -> RunResult:
        msg = f"Stopped at user request. {self._abort_reason}".strip()
        self.session.run_end("aborted", msg)
        return RunResult(RunStatus.ABORTED, msg, step, self.state)

    async def run(self, user_request: str) -> RunResult:
        self.state.goal = user_request
        self.session.prompt(user_request)
        self.context.add_user(user_request)
        self.bus.emit(EventType.USER_TEXT_INPUT, text=user_request)

        # max_steps None or <= 0 means no limit (run until complete/blocked or a
        # user interrupt). Otherwise cap at max_steps.
        unlimited = self.max_steps is None or self.max_steps <= 0
        steps = itertools.count(1) if unlimited else range(1, self.max_steps + 1)
        for step in steps:
            self.bus.emit(EventType.AGENT_STEP_STARTED, step=step)
            if not self._checkpoint("before_model_call"):
                return self._aborted_result(step)
            if self.context.needs_compaction():
                self.context.compact(self.state)

            messages = self.context.build()
            tools = self.executor.registry.specs()
            try:
                response = await asyncio.to_thread(self.provider.complete, messages, tools)
            except ProviderError as exc:
                self.session.error(str(exc))
                return RunResult(RunStatus.ERROR, str(exc), step, self.state)

            self.session.model_response(response.text, response.tool_calls, response.stop_reason)
            self.context.add_assistant(response)
            if response.text:
                self.reporter.assistant_text(response.text)

            if not response.tool_calls:
                if not self._checkpoint("before_finalizing"):
                    return self._aborted_result(step)
                self.session.run_end("complete", response.text)
                self.bus.emit(EventType.AGENT_STEP_FINISHED, step=step, final=True)
                return RunResult(RunStatus.COMPLETE, response.text, step, self.state)

            self._run_tool_calls(response)
            self.bus.emit(EventType.AGENT_STEP_FINISHED, step=step)
            if self._aborted:
                return self._aborted_result(step)

        msg = "Reached the step limit before completing."
        self.session.run_end("max_steps", msg)
        return RunResult(RunStatus.MAX_STEPS, msg, self.max_steps, self.state)

    # -- tool execution ----------------------------------------------------

    def _run_tool_calls(self, response) -> None:
        revise_feedback: str | None = None
        for tc in response.tool_calls:
            tool = self.executor.registry.get(tc.name)
            capability = tool.capability.value if tool else "unknown"
            self.session.record("tool_call", name=tc.name, arguments=tc.arguments)
            self.reporter.tool_started(tc.name, _arg_summary(tc.arguments))
            self.bus.emit(EventType.TOOL_STARTED, name=tc.name)

            # Safe checkpoint before executing any tool (covers writes, shell,
            # and sub-agent spawns). A hard interrupt stops the batch here.
            if not self._checkpoint(f"before_tool:{tc.name}"):
                note = "Skipped: user interrupted."
                self.context.add_tool_result(tc.id, tc.name, note)
                self.session.tool_result(tc.name, False, note, None, {})
                continue

            if revise_feedback is not None:
                # A prior call in this batch was redirected; don't run the rest.
                note = f"Skipped: user redirected ({revise_feedback})."
                self.context.add_tool_result(tc.id, tc.name, note)
                self.session.tool_result(tc.name, False, note, None, {})
                continue

            outcome = self.executor.execute(
                tc.name, tc.arguments, rationale=response.text or None
            )
            self.session.permission_decision(
                tc.name, capability, outcome.outcome.value, outcome.reason
            )
            self.state.note_permission(f"{tc.name}: {outcome.outcome.value}")
            self.reporter.permission(tc.name, outcome.outcome.value, outcome.reason)
            self.bus.emit(
                EventType.PERMISSION_DECISION, tool=tc.name, outcome=outcome.outcome.value
            )

            if outcome.outcome == OutcomeKind.REVISE:
                revise_feedback = outcome.feedback or "do something else"
                note = f"Not run: user chose to do something else ({revise_feedback})."
                self.context.add_tool_result(tc.id, tc.name, note)
                self.session.tool_result(tc.name, False, note, None, {})
                continue

            result = outcome.result
            assert result is not None
            shown = result.output if result.ok else (result.error or "failed")
            self.context.add_tool_result(tc.id, tc.name, shown)
            self.session.tool_result(
                tc.name, result.ok, result.output, result.error, result.metadata
            )
            self.state.note_files(result.metadata.get("files_changed"))
            self.state.note_command(result.metadata.get("command"))
            if not result.ok:
                self.state.note_error(result.error)
            self.reporter.tool_finished(
                tc.name, result.ok, result.error or f"{len(result.output)} chars"
            )
            self.bus.emit(
                EventType.TOOL_FINISHED if result.ok else EventType.TOOL_FAILED, name=tc.name
            )
            # Safe checkpoint after each tool result.
            self._checkpoint(f"after_tool:{tc.name}")

        if revise_feedback is not None:
            self.context.add_user(f"[steering] {revise_feedback}")
            self.session.steering(revise_feedback)


_UNSET_STEPS = object()  # sentinel: caller didn't pass max_steps (use the effort profile)


def assemble_runtime(
    *,
    config: JarvisConfig,
    sandbox: Sandbox,
    mode: PermissionMode,
    prompter=None,
    provider=None,
    http_client=None,
    session: SessionWriter | None = None,
    reporter: Reporter | None = None,
    instructions: InstructionQueue | None = None,
    bus: EventBus | None = None,
    effort: Effort = Effort.MEDIUM,
    max_steps: object = _UNSET_STEPS,
) -> AgentRuntime:
    """Wire up a runtime. ``provider`` may be injected (tests); else built from config.

    ``effort`` tunes step ceiling, response token budget, sub-agent availability,
    and a system-prompt reasoning nudge. An explicit ``max_steps`` overrides the
    effort profile's ceiling.
    """
    profile = profile_for(effort)
    steps = profile.max_steps if max_steps is _UNSET_STEPS else max_steps  # type: ignore[assignment]

    bus = bus if bus is not None else EventBus()
    instructions = instructions if instructions is not None else InstructionQueue(bus)
    registry = build_default_registry(include_subagents=profile.use_subagents)
    permissions = PermissionManager(mode, prompter=prompter)
    if provider is None:
        provider = build_provider(config, http_client=http_client, max_tokens=profile.max_tokens)
    if session is None:
        session = SessionWriter(sandbox)

    memory = load_project_memory(sandbox)
    system_prompt = build_system_prompt(
        mode=mode,
        sandbox_root=str(sandbox.root),
        tool_specs=registry.specs(),
        memory=memory,
        effort_note=profile.guidance,
    )
    context = ConversationContext(system_prompt)
    state = RunState()

    # Lazy import avoids an import cycle (subagents imports agent symbols).
    from jarvis_code.subagents import SubAgentOrchestrator

    orchestrator = SubAgentOrchestrator(
        provider=provider,
        sandbox=sandbox,
        parent_mode=mode,
        prompter=prompter,
        bus=bus,
        parent_session=session,
        parent_state=state,
    )
    tool_ctx = ToolContext(sandbox=sandbox, orchestrator=orchestrator)
    executor = ToolExecutor(registry, permissions, tool_ctx)
    return AgentRuntime(
        provider=provider,
        executor=executor,
        context=context,
        session=session,
        state=state,
        max_steps=steps,  # type: ignore[arg-type]
        reporter=reporter,
        instructions=instructions,
        bus=bus,
    )
