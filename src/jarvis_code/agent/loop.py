"""The agent runtime loop.

Per step: drain steering -> compact if needed -> call model -> record + add
assistant turn -> if no tool calls, finish -> else route each tool call through
the permission manager, execute allowed ones, append results, and fold any
"Do Something Else" feedback back in as steering. Repeat until complete, blocked,
or the step limit.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum

from jarvis_code.config import JarvisConfig
from jarvis_code.models import ProviderError, build_provider
from jarvis_code.permissions import OutcomeKind, PermissionManager, PermissionMode
from jarvis_code.sandbox import Sandbox
from jarvis_code.sessions import SessionWriter
from jarvis_code.tools import ToolContext, ToolExecutor, build_default_registry
from jarvis_code.utils.logging import get_logger

from .context import ConversationContext
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
        max_steps: int = 20,
        reporter: Reporter | None = None,
    ) -> None:
        self.provider = provider
        self.executor = executor
        self.context = context
        self.session = session
        self.state = state
        self.max_steps = max_steps
        self.reporter: Reporter = reporter or NoopReporter()
        self.pending_steering: list[str] = []  # Phase 9 feeds this; loop drains it.

    def steer(self, instruction: str) -> None:
        """Queue a steering instruction to fold in at the next safe checkpoint."""
        self.pending_steering.append(instruction)

    def _drain_steering(self) -> None:
        while self.pending_steering:
            instruction = self.pending_steering.pop(0)
            self.context.add_user(f"[steering] {instruction}")
            self.session.steering(instruction)

    async def run(self, user_request: str) -> RunResult:
        self.state.goal = user_request
        self.session.prompt(user_request)
        self.context.add_user(user_request)

        for step in range(1, self.max_steps + 1):
            self._drain_steering()
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
                self.session.run_end("complete", response.text)
                return RunResult(RunStatus.COMPLETE, response.text, step, self.state)

            self._run_tool_calls(response)

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

        if revise_feedback is not None:
            self.context.add_user(f"[steering] {revise_feedback}")
            self.session.steering(revise_feedback)


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
    max_steps: int = 20,
) -> AgentRuntime:
    """Wire up a runtime. ``provider`` may be injected (tests); else built from config."""
    registry = build_default_registry()
    permissions = PermissionManager(mode, prompter=prompter)
    if provider is None:
        provider = build_provider(config, http_client=http_client)
    if session is None:
        session = SessionWriter(sandbox)

    memory = load_project_memory(sandbox)
    system_prompt = build_system_prompt(
        mode=mode,
        sandbox_root=str(sandbox.root),
        tool_specs=registry.specs(),
        memory=memory,
    )
    context = ConversationContext(system_prompt)
    state = RunState()
    executor = ToolExecutor(registry, permissions, ToolContext(sandbox=sandbox))
    return AgentRuntime(
        provider=provider,
        executor=executor,
        context=context,
        session=session,
        state=state,
        max_steps=max_steps,
        reporter=reporter,
    )
