"""spawn_subagent: hand a focused task to a sub-agent.

The real orchestration arrives in Phase 10. This tool is wired and permission-
gated now so the capability is part of the matrix. When ``ctx.orchestrator`` is
present it delegates; otherwise it returns a clear, non-crashing message.
"""

from __future__ import annotations

from jarvis_code.permissions.modes import Capability

from .base import Tool, ToolContext, ToolResult

_AGENT_TYPES = ["explorer", "planner", "debugger", "reviewer", "implementer", "verifier"]


class SpawnSubagentTool(Tool):
    name = "spawn_subagent"
    description = (
        "Launch a focused sub-agent in its own context. It inherits the same "
        "sandbox and permission mode and never exceeds the parent's permissions. "
        f"agent_type is one of: {', '.join(_AGENT_TYPES)}."
    )
    capability = Capability.SUBAGENT
    parameters = {
        "type": "object",
        "properties": {
            "agent_type": {"type": "string", "enum": _AGENT_TYPES},
            "task": {"type": "string", "description": "Concise task for the sub-agent."},
        },
        "required": ["agent_type", "task"],
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        agent_type = str(self._require(args, "agent_type"))
        task = str(self._require(args, "task"))
        if agent_type not in _AGENT_TYPES:
            return ToolResult.fail(
                f"unknown agent_type '{agent_type}'; choose one of {_AGENT_TYPES}"
            )
        orchestrator = ctx.orchestrator
        if orchestrator is None:
            return ToolResult.success(
                f"Sub-agent orchestration is not enabled yet (Phase 10). "
                f"Would launch a '{agent_type}' for: {task}",
                agent_type=agent_type,
                deferred=True,
            )
        # Phase 10 wires this: orchestrator.spawn(agent_type, task) -> summary.
        summary = orchestrator.spawn(agent_type, task)  # type: ignore[attr-defined]
        return ToolResult.success(summary, agent_type=agent_type)
