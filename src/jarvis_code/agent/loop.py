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
import threading
from dataclasses import dataclass
from enum import Enum

from jarvis_code.config import Effort, JarvisConfig
from jarvis_code.events import EventBus, EventType, InstructionKind, InstructionQueue
from jarvis_code.models import ProviderError, build_provider
from jarvis_code.permissions import Decision, OutcomeKind, PermissionManager, PermissionMode
from jarvis_code.sandbox import Sandbox
from jarvis_code.sessions import SessionWriter
from jarvis_code.tools import ToolContext, ToolExecutor, build_default_registry
from jarvis_code.utils.logging import get_logger
from jarvis_code.utils.threads import run_in_daemon_thread

from .context import ConversationContext
from .effort import profile_for
from .memory import load_project_memory
from .prompts import build_system_prompt
from .reporter import NoopReporter, Reporter
from .state import RunState

_log = get_logger("agent")

# How often a waiting model call checks whether the user asked to stop.
_INTERRUPT_POLL_SECONDS = 0.1

# Provider stop reasons meaning "the response hit the output-token limit".
_TRUNCATED_STOP_REASONS = {"max_tokens", "length", "MAX_TOKENS"}

_TRUNCATED_TOOL_NOTE = (
    "Not run: your response hit the output-token limit, so this tool call may "
    "have been cut off mid-arguments. Re-issue it with less content per call — "
    "e.g. write a large file in parts (write_file the first section, then "
    "edit_file to append the rest)."
)


# Give up (rather than loop forever on an unlimited-step run) when the model
# cannot fit a tool call into its response budget this many times in a row.
_MAX_CONSECUTIVE_TRUNCATIONS = 3


class _Interrupted(Exception):
    """The user asked to stop while we were waiting on the model."""


def _arg_summary(args: dict) -> str:
    """A short one-line summary of tool arguments for step display."""
    if not isinstance(args, dict):
        return ""
    for key in ("path", "command", "pattern", "src"):
        if key in args:
            return str(args[key])[:80]
    return ""


def _result_for_model(result) -> str:
    """What the model is shown for a tool result.

    A failure keeps its output: a failing test run or compiler error exits
    non-zero, and the output *is* the information the model needs to fix it —
    "exit code 1" alone is useless.
    """
    if result.ok:
        return result.output or "(no output)"
    error = result.error or "failed"
    return f"{error}\n{result.output}" if result.output else error


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
        interrupt: threading.Event | None = None,
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
        # Set (from any thread or a signal handler) to stop the current turn: the
        # CLI's Ctrl-C handler sets it. A sub-agent shares its parent's event so
        # one Ctrl-C stops the whole tree; only the owner resets it per turn.
        self._owns_interrupt = interrupt is None
        self.interrupt = interrupt if interrupt is not None else threading.Event()
        #: True while a turn is running (``run`` has not returned yet).
        self.busy = False
        #: True while tools run synchronously on the calling thread — the only
        #: time a KeyboardInterrupt is needed to break out of a blocking call.
        self.in_tool_section = False

    def steer(self, instruction: str) -> None:
        """Queue a steering instruction to fold in at the next safe checkpoint."""
        self.instructions.submit(instruction)

    def _checkpoint(self, name: str) -> bool:
        """Drain + apply live instructions. Returns False if the run must abort.

        Safe checkpoints (the loop calls this before the model call, before each
        tool execution, after each tool result, and before finalizing).
        """
        if self.interrupt.is_set() and not self._aborted:
            self._aborted = True
            self._abort_reason = "Interrupted."
            self.bus.emit(EventType.INTERRUPT_REQUESTED, kind="hard", at=name)
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

    def _thinking(self, active: bool) -> None:
        # Optional hook: older/third-party reporters may not implement it.
        hook = getattr(self.reporter, "thinking", None)
        if hook is not None:
            hook(active)

    async def _call_model(self, messages, tools):
        """Call the provider without blocking the loop — and without being stuck.

        The request runs in a daemon thread while we poll for a stop request, so
        "stop" / Ctrl-C take effect immediately instead of after a (possibly
        minutes-long) generation, and an abandoned request never delays exit.
        """
        future = run_in_daemon_thread(self.provider.complete, messages, tools)
        try:
            while True:
                done, _ = await asyncio.wait({future}, timeout=_INTERRUPT_POLL_SECONDS)
                if done:
                    return future.result()
                if self.interrupt.is_set() or self.instructions.has_hard_interrupt():
                    raise _Interrupted
        finally:
            if not future.done():
                future.cancel()  # stop waiting; the thread's result is dropped

    async def run(self, user_request: str) -> RunResult:
        """Run one turn. Never leaves the conversation in a state a provider rejects."""
        self.busy = True
        try:
            return await self._run(user_request)
        finally:
            self.busy = False
            self.in_tool_section = False

    async def _run(self, user_request: str) -> RunResult:
        # A previous turn's stop must not poison this one (the runtime is reused
        # across chat turns), and anything it left half-done is closed out first.
        self._aborted = False
        self._abort_reason = ""
        if self._owns_interrupt:
            self.interrupt.clear()
        self.context.close_dangling_tool_calls()
        self.state.goal = user_request
        self.executor.ctx.questions_asked = 0  # fresh clarification budget per turn
        self.session.prompt(user_request)
        self.context.add_user(user_request)
        self.bus.emit(EventType.USER_TEXT_INPUT, text=user_request)

        # max_steps None or <= 0 means no limit (run until complete/blocked or a
        # user interrupt). Otherwise cap at max_steps.
        unlimited = self.max_steps is None or self.max_steps <= 0
        steps = itertools.count(1) if unlimited else range(1, self.max_steps + 1)
        truncations = 0  # consecutive responses cut off mid tool call
        for step in steps:
            self.bus.emit(EventType.AGENT_STEP_STARTED, step=step)
            if not self._checkpoint("before_model_call"):
                return self._aborted_result(step)
            if self.context.needs_compaction():
                self.context.compact(self.state)

            messages = self.context.build()
            tools = self.executor.registry.specs()
            self._thinking(True)
            try:
                response = await self._call_model(messages, tools)
            except _Interrupted:
                self._checkpoint("during_model_call")  # consume a queued "stop"
                self._aborted = True
                self._abort_reason = self._abort_reason or "Interrupted."
                return self._aborted_result(step)
            except ProviderError as exc:
                self.session.error(str(exc))
                return RunResult(RunStatus.ERROR, str(exc), step, self.state)
            except Exception as exc:
                # A provider bug / unexpected response shape must not take the
                # whole session down with a traceback.
                msg = f"model call failed: {type(exc).__name__}: {exc}"
                _log.warning("%s", msg)
                self.session.error(msg)
                return RunResult(RunStatus.ERROR, msg, step, self.state)
            finally:
                self._thinking(False)

            self.session.model_response(response.text, response.tool_calls, response.stop_reason)
            self.context.add_assistant(response)
            if response.text:
                self.reporter.assistant_text(response.text)
            truncated = response.stop_reason in _TRUNCATED_STOP_REASONS

            if not response.tool_calls:
                if not self._checkpoint("before_finalizing"):
                    return self._aborted_result(step)
                if truncated:
                    self.reporter.info(
                        "note: the response hit the output-token limit and may be cut off "
                        "(raise --effort for a larger budget)."
                    )
                self.session.run_end("complete", response.text)
                self.bus.emit(EventType.AGENT_STEP_FINISHED, step=step, final=True)
                return RunResult(RunStatus.COMPLETE, response.text, step, self.state)

            if truncated:
                # The tool-call JSON was probably cut off: running it could write
                # half a file. Don't — tell the model to send smaller pieces.
                self._skip_tool_calls(response, _TRUNCATED_TOOL_NOTE)
                self.bus.emit(EventType.AGENT_STEP_FINISHED, step=step)
                truncations += 1
                if truncations >= _MAX_CONSECUTIVE_TRUNCATIONS:
                    msg = (
                        "The model keeps hitting its output-token limit before finishing "
                        "a tool call. Try a higher --effort (larger response budget) or "
                        "ask for the work in smaller pieces."
                    )
                    self.session.error(msg)
                    return RunResult(RunStatus.ERROR, msg, step, self.state)
                continue
            truncations = 0

            self.in_tool_section = True
            try:
                self._run_tool_calls(response)
            except KeyboardInterrupt:
                # Ctrl-C while a tool was blocking (a shell command, an approval
                # prompt). Stop the turn cleanly instead of dying with the chat.
                self._aborted = True
                self._abort_reason = "Interrupted."
                self.context.close_dangling_tool_calls(
                    "Interrupted by the user before this tool finished."
                )
            finally:
                self.in_tool_section = False
            self.bus.emit(EventType.AGENT_STEP_FINISHED, step=step)
            if self._aborted:
                return self._aborted_result(step)

        msg = "Reached the step limit before completing."
        self.session.run_end("max_steps", msg)
        return RunResult(RunStatus.MAX_STEPS, msg, self.max_steps, self.state)

    # -- tool execution ----------------------------------------------------

    def _skip_tool_calls(self, response, note: str) -> None:
        """Answer every tool call in ``response`` with ``note`` without running it."""
        for tc in response.tool_calls:
            self.session.record("tool_call", name=tc.name, arguments=tc.arguments)
            self.context.add_tool_result(tc.id, tc.name, note)
            self.session.tool_result(tc.name, False, note, None, {})
        self.reporter.info("note: response was cut off by the output-token limit — retrying in smaller pieces.")

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

            # Show the boxed, risk-coloured command (or the "updates" diff) before
            # it runs — unless policy will deny it outright.
            request = self.executor.preview(tc.name, tc.arguments, response.text or None)
            if request is not None and (
                self.executor.permissions.decide(request.capability) is not Decision.DENY
            ):
                self.reporter.action(request)
            outcome = self.executor.execute(
                tc.name, tc.arguments, rationale=response.text or None, request=request
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
            self.context.add_tool_result(tc.id, tc.name, _result_for_model(result))
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
    asker: object | None = None,
) -> AgentRuntime:
    """Wire up a runtime. ``provider`` may be injected (tests); else built from config.

    ``effort`` tunes step ceiling, response token budget, sub-agent availability,
    and a system-prompt reasoning nudge. An explicit ``max_steps`` overrides the
    effort profile's ceiling. ``asker`` (a ``str -> str | None`` callable) enables
    the ``ask_user`` clarification tool when an interactive user is present.
    """
    profile = profile_for(effort)
    steps = profile.max_steps if max_steps is _UNSET_STEPS else max_steps  # type: ignore[assignment]

    bus = bus if bus is not None else EventBus()
    instructions = instructions if instructions is not None else InstructionQueue(bus)
    registry = build_default_registry(
        include_subagents=profile.use_subagents, include_ask=asker is not None
    )
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
        # In-character spoken narration only when voice output + intent are on.
        narrate=config.voice.enabled and config.voice.speak_intent,
        can_ask=asker is not None,
    )
    context = ConversationContext(system_prompt)
    state = RunState()

    # Lazy import avoids an import cycle (subagents imports agent symbols).
    from jarvis_code.subagents import SubAgentOrchestrator

    interrupt = threading.Event()  # shared with sub-agents: one Ctrl-C stops all
    orchestrator = SubAgentOrchestrator(
        provider=provider,
        sandbox=sandbox,
        parent_mode=mode,
        prompter=prompter,
        bus=bus,
        parent_session=session,
        parent_state=state,
        interrupt=interrupt,
    )
    tool_ctx = ToolContext(sandbox=sandbox, orchestrator=orchestrator, asker=asker)
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
        interrupt=interrupt,
    )
