"""Prompter implementations.

The terminal prompter renders the exact 3-choice menu the instructions require:
``1) Yes  2) No  3) Do Something Else``. The non-interactive prompters are for
``dangerous`` mode (never asks) and for tests.
"""

from __future__ import annotations

from collections.abc import Callable

from .manager import Choice, PermissionRequest, PermissionResponse


class AutoApprovePrompter:
    """Always approves. Used internally where policy already decided ALLOW."""

    def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        return PermissionResponse(choice=Choice.YES)


class DenyPrompter:
    """Always declines. Useful as a safe default and in tests."""

    def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        return PermissionResponse(choice=Choice.NO)


class ScriptedPrompter:
    """Returns pre-scripted responses in order (tests)."""

    def __init__(self, responses: list[PermissionResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[PermissionRequest] = []

    def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("ScriptedPrompter ran out of responses")
        return self._responses.pop(0)


def render_request(request: PermissionRequest) -> str:
    """Render a request as the human-facing approval block (no secrets)."""
    lines = [f"Requesting permission: {request.summary}"]
    if request.command is not None:
        lines.append(f"  command:         {request.command}")
    if request.cwd is not None:
        lines.append(f"  working dir:     {request.cwd}")
    if request.why is not None:
        lines.append(f"  why:             {request.why}")
    if request.expected_effect is not None:
        lines.append(f"  expected effect: {request.expected_effect}")
    lines.append(f"  risk level:      {request.risk.value}")
    if request.diff:
        lines.append("  diff:")
        lines.extend(f"    {ln}" for ln in request.diff.splitlines())
    lines.append("  1) Yes   2) No   3) Do Something Else")
    return "\n".join(lines)


class TerminalPrompter:
    """Interactive 1/2/3 menu over injectable input/output callables."""

    def __init__(
        self,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self._input = input_fn
        self._output = output_fn

    def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        self._output(render_request(request))
        while True:
            raw = self._input("Choose [1/2/3]: ").strip().lower()
            if raw in ("1", "y", "yes"):
                return PermissionResponse(choice=Choice.YES)
            if raw in ("2", "n", "no"):
                return PermissionResponse(choice=Choice.NO)
            if raw in ("3", "e", "else"):
                feedback = self._input("What should I do instead? ").strip()
                return PermissionResponse(
                    choice=Choice.SOMETHING_ELSE, feedback=feedback or None
                )
            self._output("Please choose 1, 2, or 3.")
