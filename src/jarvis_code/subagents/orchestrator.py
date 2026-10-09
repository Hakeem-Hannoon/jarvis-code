"""Sub-agent orchestrator.

Each sub-agent runs in its own context window with a fresh transcript, inherits
the SAME sandbox root, and runs at a mode clamped to never exceed the parent's.
The parent receives only a concise summary — never the sub-agent's transcript.
Sub-agents cannot spawn further sub-agents (no nested orchestrator), bounding
recursion.
"""

from __future__ import annotations

import asyncio
import threading

from jarvis_code.agent.context import ConversationContext
from jarvis_code.agent.loop import AgentRuntime
from jarvis_code.agent.prompts import build_system_prompt
from jarvis_code.agent.state import RunState
from jarvis_code.events import EventBus, EventType
from jarvis_code.permissions import PermissionManager, PermissionMode
from jarvis_code.sandbox import Sandbox
from jarvis_code.sessions import SessionWriter
from jarvis_code.tools import ToolContext, ToolExecutor, build_default_registry
from jarvis_code.utils.logging import get_logger

from .types import AGENT_ROLES, AGENT_TYPES, clamp_mode

_log = get_logger("subagents")

_SUMMARY_MAX = 1200


def _run_blocking(coro):
    """Run a coroutine to completion from sync code, even inside a running loop.

    The parent loop calls tools synchronously; running the sub-agent on its own
    loop in a worker thread avoids 'loop already running' and keeps isolation.
    """
    box: dict = {}

    def runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as exc:  # surfaced to the caller below
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["result"]


class SubAgentOrchestrator:
    def __init__(
        self,
        *,
        provider,
        sandbox: Sandbox,
        parent_mode: PermissionMode,
        prompter=None,
        bus: EventBus | None = None,
        voice=None,
        parent_session: SessionWriter | None = None,
        parent_state: RunState | None = None,
        max_steps: int = 8,
        interrupt: threading.Event | None = None,
    ) -> None:
        self.provider = provider
        self.sandbox = sandbox
        self.parent_mode = parent_mode
        self.prompter = prompter
        self.bus = bus
        self.voice = voice
        self.parent_session = parent_session
        self.parent_state = parent_state
        self.max_steps = max_steps
        self.interrupt = interrupt  # the parent's stop signal (Ctrl-C), if any
        self._count = 0

    def effective_mode(self, requested: PermissionMode | None = None) -> PermissionMode:
        """The sub-agent's mode — clamped to never exceed the parent's."""
        return clamp_mode(self.parent_mode, requested)

    def spawn(self, agent_type: str, task: str, *, swarm: bool = False) -> str:
        if agent_type not in AGENT_TYPES:
            return f"Unknown sub-agent type '{agent_type}'. Choose one of {list(AGENT_TYPES)}."

        self._count += 1
        if self.voice is not None:
            self.voice.announce_subagent(swarm=swarm)
        if self.bus is not None:
            self.bus.emit(EventType.SUBAGENT_STARTED, agent_type=agent_type)

        sub = self._build_subruntime(agent_type)
        try:
            result = _run_blocking(sub.run(task))
        except Exception as exc:
            # A sub-agent crash is reported to the parent, not raised through it.
            _log.warning("sub-agent '%s' crashed: %s: %s", agent_type, type(exc).__name__, exc)
            if self.bus is not None:
                self.bus.emit(EventType.SUBAGENT_FINISHED, agent_type=agent_type)
            return f"[{agent_type}] failed: {type(exc).__name__}: {exc}"
        summary = self._summarize(agent_type, result)

        if self.parent_session is not None:
            self.parent_session.subagent_summary(agent_type, summary)
        if self.parent_state is not None:
            self.parent_state.note_subagent(f"{agent_type}: {summary}")
        if self.bus is not None:
            self.bus.emit(EventType.SUBAGENT_FINISHED, agent_type=agent_type)
        _log.info("sub-agent '%s' finished (%s)", agent_type, result.status.value)
        return summary

    # -- internals ---------------------------------------------------------

    def _build_subruntime(self, agent_type: str) -> AgentRuntime:
        mode = self.effective_mode()  # inherit parent, clamped
        # No spawn_subagent (recursion is bounded at one level) and no ask_user
        # (a sub-agent has no asker): don't advertise tools that can't work.
        registry = build_default_registry(include_subagents=False, include_ask=False)
        permissions = PermissionManager(mode, prompter=self.prompter)
        system_prompt = build_system_prompt(
            mode=mode,
            sandbox_root=str(self.sandbox.root),
            tool_specs=registry.specs(),
            memory=None,
            role=AGENT_ROLES[agent_type],
        )
        context = ConversationContext(system_prompt)
        # Sub-agents share the SAME sandbox but cannot spawn further sub-agents.
        ctx = ToolContext(sandbox=self.sandbox, orchestrator=None)
        executor = ToolExecutor(registry, permissions, ctx)
        # Keyed by the parent session so two runs never append to the same file.
        parent_id = self.parent_session.session_id if self.parent_session else "nosession"
        session = SessionWriter(
            self.sandbox, session_id=f"{parent_id}-sub-{agent_type}-{self._count}"
        )
        return AgentRuntime(
            provider=self.provider,
            executor=executor,
            context=context,
            session=session,
            state=RunState(),
            max_steps=self.max_steps,
            bus=self.bus,
            interrupt=self.interrupt,
            shares_interrupt=self.interrupt is not None,
        )

    def _summarize(self, agent_type: str, result) -> str:
        """A concise summary for the parent — never the full transcript."""
        text = (result.final_text or "").strip()
        extras = []
        if result.state.files_changed:
            extras.append("files: " + ", ".join(result.state.files_changed))
        if result.state.errors:
            extras.append(f"errors: {len(result.state.errors)}")
        summary = text
        if extras:
            summary = (summary + "\n" if summary else "") + " | ".join(extras)
        summary = summary or f"{agent_type} finished with no output."
        if len(summary) > _SUMMARY_MAX:
            summary = summary[:_SUMMARY_MAX] + " …[truncated]"
        return f"[{agent_type}] {summary}"
