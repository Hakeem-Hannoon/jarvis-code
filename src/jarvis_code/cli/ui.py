"""Terminal UX: concise step display, a console reporter, and a finish summary."""

from __future__ import annotations

import typer

from jarvis_code.agent import RunResult, RunStatus
from jarvis_code.agent.state import RunState
from jarvis_code.permissions.manager import RiskLevel

# Risk → colour, ranked green (low) · yellow (medium) · red (high).
_RISK_COLOR = {
    RiskLevel.LOW: typer.colors.GREEN,
    RiskLevel.MEDIUM: typer.colors.YELLOW,
    RiskLevel.HIGH: typer.colors.RED,
}

# Tool → (human verb, colour) for the live "what it's touching" tree. run_shell
# is intentionally absent: its boxed command (see `action`) is the display.
_TOOL_VERB = {
    "read_file": ("reading", typer.colors.BLUE),
    "list_files": ("listing", typer.colors.BLUE),
    "write_file": ("writing", typer.colors.GREEN),
    "create_directory": ("creating", typer.colors.GREEN),
    "edit_file": ("editing", typer.colors.YELLOW),
    "move_file": ("moving", typer.colors.YELLOW),
    "delete_file": ("deleting", typer.colors.RED),
    "search_files": ("searching", typer.colors.MAGENTA),
    "grep_text": ("searching", typer.colors.MAGENTA),
    "git_status": ("git status", typer.colors.CYAN),
    "git_diff": ("git diff", typer.colors.CYAN),
    "spawn_subagent": ("delegating to", typer.colors.MAGENTA),
}


def render_command_box(command: str, why: str | None, risk: RiskLevel, echo) -> None:
    """Print the exact shell command in a box, with its reasoning under it.

    The whole panel is coloured by risk (green/yellow/red) so the user can gauge
    a command at a glance before it runs.
    """
    color = _RISK_COLOR.get(risk, typer.colors.GREEN)
    bar = typer.style("│", fg=color)
    echo(typer.style(f"  ┌─ shell · risk: {risk.value.upper()} " + "─" * 8, fg=color, bold=True))
    for line in (command.splitlines() or [command]):
        echo(f"  {bar} " + typer.style(line, bold=True))
    if why:
        why = why.strip().splitlines()[0] if why.strip() else ""
        if why:
            echo(f"  {bar} " + typer.style(f"why: {why}", fg=color))
    echo(typer.style("  └" + "─" * 24, fg=color))


def render_updates(diff: str, echo) -> None:
    """Print a unified diff under an "updates" label: additions green, deletions red."""
    echo(typer.style("  updates", fg=typer.colors.CYAN, bold=True))
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            echo(typer.style("  " + line, dim=True))
        elif line.startswith("@@"):
            echo(typer.style("  " + line, fg=typer.colors.CYAN))
        elif line.startswith("+"):
            echo(typer.style("  " + line, fg=typer.colors.GREEN))
        elif line.startswith("-"):
            echo(typer.style("  " + line, fg=typer.colors.RED))
        else:
            echo(typer.style("  " + line, dim=True))


class ConsoleReporter:
    """Prints concise, curated step lines as the agent works."""

    def __init__(self, echo=typer.echo) -> None:
        self._echo = echo

    def assistant_text(self, text: str) -> None:
        text = text.strip()
        if text:
            self._echo(typer.style("• ", fg=typer.colors.CYAN) + text)

    def tool_started(self, name: str, summary: str) -> None:
        # A live, tree-style line of what the agent is touching — "reading
        # src/app.py", "writing src/new.py" — so multiple files read as a tree.
        if name == "run_shell":
            return  # its boxed command (see `action`) is the display
        verb, color = _TOOL_VERB.get(name, (name, typer.colors.BLUE))
        target = typer.style(f" {summary}", dim=True) if summary else ""
        self._echo(typer.style(f"  ├─ {verb}", fg=color, bold=True) + target)

    def action(self, request: object) -> None:
        """Render a shell command box, or an edit's "updates" diff, before it runs."""
        command = getattr(request, "command", None)
        diff = getattr(request, "diff", None)
        if command is not None:
            render_command_box(command, getattr(request, "why", None),
                               getattr(request, "risk", RiskLevel.LOW), self._echo)
        elif diff:
            render_updates(diff, self._echo)

    def tool_finished(self, name: str, ok: bool, detail: str) -> None:
        mark = typer.style("✓", fg=typer.colors.GREEN) if ok else typer.style("✗", fg=typer.colors.RED)
        self._echo(f"    {mark} {name} ({detail})")

    def permission(self, tool: str, outcome: str, reason: str | None) -> None:
        if outcome == "denied":
            self._echo(typer.style(f"    ⨯ {tool} denied: {reason}", fg=typer.colors.YELLOW))

    def info(self, text: str) -> None:
        self._echo(typer.style(text, fg=typer.colors.MAGENTA))


class CompositeReporter:
    """Fan out reporter hooks to several reporters (e.g. console + voice)."""

    def __init__(self, reporters: list) -> None:
        self._reporters = reporters

    def assistant_text(self, text: str) -> None:
        for r in self._reporters:
            r.assistant_text(text)

    def tool_started(self, name: str, summary: str) -> None:
        for r in self._reporters:
            r.tool_started(name, summary)

    def action(self, request: object) -> None:
        for r in self._reporters:
            r.action(request)

    def tool_finished(self, name: str, ok: bool, detail: str) -> None:
        for r in self._reporters:
            r.tool_finished(name, ok, detail)

    def permission(self, tool: str, outcome: str, reason: str | None) -> None:
        for r in self._reporters:
            r.permission(tool, outcome, reason)

    def info(self, text: str) -> None:
        for r in self._reporters:
            r.info(text)


def print_finish_summary(result: RunResult) -> None:
    state: RunState = result.state
    typer.echo("")
    status_color = {
        RunStatus.COMPLETE: typer.colors.GREEN,
        RunStatus.MAX_STEPS: typer.colors.YELLOW,
        RunStatus.ERROR: typer.colors.RED,
        RunStatus.ABORTED: typer.colors.YELLOW,
    }[result.status]
    typer.echo(typer.style(f"── {result.status.value.upper()} ──", fg=status_color, bold=True))

    if result.final_text.strip():
        typer.echo(result.final_text.strip())

    typer.echo("")
    typer.echo(typer.style("Summary:", bold=True))
    typer.echo(f"  steps:          {result.steps}")
    typer.echo(f"  files touched:  {', '.join(state.files_changed) or '(none)'}")
    typer.echo(f"  commands run:   {len(state.commands_run)}")
    if state.errors:
        typer.echo(f"  errors:         {len(state.errors)}")
    if state.subagent_summaries:
        typer.echo(f"  sub-agents:     {len(state.subagent_summaries)}")
