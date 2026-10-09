"""The approval prompt as the CLI draws it.

Same prompt, same three choices, same information the invariants require
(command, working dir, why, expected effect, risk; or the diff for a file
change) — laid out as one risk-coloured panel instead of a wall of labels::

      ╭─ approve · shell command ──────────── risk: LOW
      │ $ pytest -q tests/test_parser.py
      │
      │ cwd     .
      │ why     Verify the parser change.
      │ effect  Executes the command; output is captured.
      ╰──

Every field is model-controlled and therefore sanitized (see ``utils.text``).
"""

from __future__ import annotations

import typer

from jarvis_code.permissions import PermissionRequest, TerminalPrompter
from jarvis_code.permissions.manager import RiskLevel
from jarvis_code.utils.text import sanitize_terminal

from .ui import diff_lines

_RISK_COLOR = {
    RiskLevel.LOW: typer.colors.GREEN,
    RiskLevel.MEDIUM: typer.colors.YELLOW,
    RiskLevel.HIGH: typer.colors.RED,
}

_HEADLINE = {
    "write_file": "write",
    "edit_file": "edit",
    "delete_file": "delete",
    "move_file": "move",
    "create_directory": "create directory",
    "spawn_subagent": "launch sub-agent",
}


def _headline(request: PermissionRequest) -> str:
    if request.command is not None:
        return "shell command"
    verb = _HEADLINE.get(request.tool_name)
    # ``summary`` is "<tool> <path>" for file tools; keep just the target.
    target = request.summary
    if target.startswith(request.tool_name):
        target = target[len(request.tool_name) :].strip()
    target = sanitize_terminal(target)
    if verb:
        return f"{verb} {target}".strip()
    return sanitize_terminal(request.summary)


def approval_lines(request: PermissionRequest) -> list[str]:
    """The styled approval panel for ``request`` (no choices line)."""
    color = _RISK_COLOR.get(request.risk, typer.colors.GREEN)
    bar = typer.style("  │", fg=color)

    def field(label: str, value: str) -> str:
        first, *rest = sanitize_terminal(value).splitlines() or [""]
        lines = [f"{bar} " + typer.style(f"{label:<7}", dim=True) + f" {first}"]
        lines += [f"{bar} " + " " * 8 + line for line in rest[:5]]
        return "\n".join(lines)

    title = f"  ╭─ approve · {_headline(request)} "
    risk = f" risk: {request.risk.value.upper()}"
    rule = "─" * max(3, 56 - len(title) - len(risk))
    out = ["", typer.style(title + rule + risk, fg=color, bold=True)]

    if request.command is not None:
        for i, line in enumerate(sanitize_terminal(request.command).splitlines() or [""]):
            prompt = typer.style("$ " if i == 0 else "  ", dim=True)
            out.append(f"{bar} {prompt}" + typer.style(line, bold=True))
        out.append(bar)
        if request.cwd is not None:
            out.append(field("cwd", str(request.cwd)))
    if request.why:
        out.append(field("why", request.why.strip()))
    if request.expected_effect:
        out.append(field("effect", request.expected_effect))
    if request.diff:
        if request.why:
            out.append(bar)
        out.extend(f"{bar} {line}" for line in diff_lines(request.diff, indent=""))
    out.append(typer.style("  ╰──", fg=color))
    return out


class ConsolePrompter(TerminalPrompter):
    """`TerminalPrompter` with the CLI's panel styling for the request body.

    ``before_prompt`` runs just before the panel is drawn; the CLI uses it to
    stop the spinner (a sub-agent can ask for approval while the parent's
    "working…" spinner is still animating on the same terminal).
    """

    def __init__(self, *args, before_prompt=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._before_prompt = before_prompt

    def body_lines(self, request: PermissionRequest) -> list[str]:
        return approval_lines(request)

    def request_permission(self, request: PermissionRequest):
        if self._before_prompt is not None:
            self._before_prompt()
        return super().request_permission(request)
