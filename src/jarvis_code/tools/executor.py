"""Glue between the permission manager and the tools.

The executor is the only place a tool runs. For every proposed tool call it:
1. looks the tool up,
2. builds a :class:`PermissionRequest` (with a diff/summary for edits and
   command/cwd/risk for shell),
3. asks the :class:`PermissionManager`, and
4. runs the tool only if the outcome is ALLOWED.

This keeps tools ignorant of permissions and keeps every gate decision in one
place.
"""

from __future__ import annotations

from dataclasses import dataclass

from jarvis_code.permissions import (
    Capability,
    OutcomeKind,
    PermissionManager,
    PermissionRequest,
    RiskLevel,
)
from jarvis_code.sandbox import SandboxViolation
from jarvis_code.utils.diffs import unified_diff

from .base import ToolContext, ToolResult
from .registry import ToolRegistry
from .shell_tool import assess_shell_risk


@dataclass
class ExecutionResult:
    """The result of attempting a tool call through the permission gate."""

    tool_name: str
    outcome: OutcomeKind
    result: ToolResult | None = None
    reason: str | None = None
    feedback: str | None = None  # set when the user chose "Do Something Else"

    @property
    def ran(self) -> bool:
        return self.result is not None


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        permissions: PermissionManager,
        ctx: ToolContext,
    ) -> None:
        self.registry = registry
        self.permissions = permissions
        self.ctx = ctx

    def execute(
        self,
        tool_name: str,
        args: dict,
        *,
        rationale: str | None = None,
    ) -> ExecutionResult:
        tool = self.registry.get(tool_name)
        if tool is None:
            return ExecutionResult(
                tool_name=tool_name,
                outcome=OutcomeKind.DENIED,
                result=ToolResult.fail(f"unknown tool '{tool_name}'"),
                reason="unknown tool",
            )

        request = self._build_request(tool_name, tool.capability, args, rationale)
        outcome = self.permissions.authorize(request)

        if outcome.kind == OutcomeKind.ALLOWED:
            result = tool.run(self.ctx, args)
            return ExecutionResult(tool_name, OutcomeKind.ALLOWED, result=result)

        if outcome.kind == OutcomeKind.REVISE:
            return ExecutionResult(
                tool_name,
                OutcomeKind.REVISE,
                reason=outcome.reason,
                feedback=outcome.feedback,
            )

        # DENIED
        return ExecutionResult(
            tool_name,
            OutcomeKind.DENIED,
            result=ToolResult.fail(f"permission denied: {outcome.reason}"),
            reason=outcome.reason,
        )

    # -- request construction ----------------------------------------------

    def _build_request(
        self,
        tool_name: str,
        capability: Capability,
        args: dict,
        rationale: str | None,
    ) -> PermissionRequest:
        if capability == Capability.SHELL:
            command = str(args.get("command", ""))
            cwd = args.get("cwd") or "."
            return PermissionRequest(
                tool_name=tool_name,
                capability=capability,
                summary="Run a shell command inside the sandbox.",
                command=command,
                cwd=cwd,
                why=rationale or "Requested by the agent.",
                expected_effect="Executes the command; output is captured.",
                risk=assess_shell_risk(command),
            )

        diff = self._preview_diff(tool_name, args)
        summary = self._summary(tool_name, args)
        return PermissionRequest(
            tool_name=tool_name,
            capability=capability,
            summary=summary,
            why=rationale,
            diff=diff,
            risk=RiskLevel.LOW,
        )

    def _summary(self, tool_name: str, args: dict) -> str:
        if tool_name in ("write_file", "edit_file", "delete_file", "create_directory"):
            return f"{tool_name} {args.get('path', '')}".strip()
        if tool_name == "move_file":
            return f"move_file {args.get('src', '')} -> {args.get('dst', '')}"
        return tool_name

    def _preview_diff(self, tool_name: str, args: dict) -> str | None:
        """Best-effort change preview for the approval prompt (read-only)."""
        sandbox = self.ctx.sandbox
        try:
            if tool_name == "write_file":
                path = args.get("path")
                if not path:
                    return None
                old = sandbox.read(path) if sandbox.exists(path) else ""
                return unified_diff(old, str(args.get("content", "")), str(path)) or None
            if tool_name == "edit_file":
                path = args.get("path")
                if not path or not sandbox.exists(path):
                    return None
                old = sandbox.read(path)
                new = old.replace(
                    str(args.get("old_string", "")), str(args.get("new_string", ""))
                )
                return unified_diff(old, new, str(path)) or None
        except (SandboxViolation, OSError, UnicodeDecodeError):
            return None
        return None
