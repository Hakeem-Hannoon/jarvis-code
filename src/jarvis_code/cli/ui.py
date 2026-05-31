"""Terminal UX: concise step display, a console reporter, and a finish summary."""

from __future__ import annotations

import typer

from jarvis_code.agent import RunResult, RunStatus
from jarvis_code.agent.state import RunState


class ConsoleReporter:
    """Prints concise, curated step lines as the agent works."""

    def __init__(self, echo=typer.echo) -> None:
        self._echo = echo

    def assistant_text(self, text: str) -> None:
        text = text.strip()
        if text:
            self._echo(typer.style("• ", fg=typer.colors.CYAN) + text)

    def tool_started(self, name: str, summary: str) -> None:
        tail = f" {summary}" if summary else ""
        self._echo(typer.style(f"  → {name}", fg=typer.colors.BLUE) + tail)

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
