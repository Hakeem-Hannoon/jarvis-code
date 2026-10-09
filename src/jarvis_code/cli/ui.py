"""Terminal UX: the live step display, command/diff rendering, and summaries.

What a turn looks like::

    ● I'll check how the parser handles nested calls.
      ├─ reading src/parser.py  · 212 lines
      ├─ searching parse_call  · 4 matches
      ┌─ shell · risk: LOW ──────────
      │ $ pytest -q tests/test_parser.py
      └──
      │ 12 passed in 0.31s
      ✓ exit 0

    ● All twelve parser tests pass.

      ✓ 4.1s · 3 steps · 2.4k in / 310 out tokens

Everything model-controlled is passed through :func:`sanitize_terminal` before
it is printed, so an escape sequence in a file name, command or reply is shown
rather than obeyed by the terminal.
"""

from __future__ import annotations

from collections.abc import Callable

import typer

from jarvis_code.agent import RunResult, RunStatus
from jarvis_code.permissions.manager import RiskLevel
from jarvis_code.utils.text import sanitize_terminal

from .markdown import print_reply, rich_available
from .spinner import Spinner, spinner_supported

Echo = Callable[[str], None]

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
    "ask_user": ("asking you", typer.colors.CYAN),
}

# Tools slow enough that their line should appear when they START, not when
# they finish (everything else is shown as one combined line on completion).
_SLOW_TOOLS = {"spawn_subagent", "ask_user"}

_BULLET = "●"
_MAX_DIFF_LINES = 120  # cap a rendered diff; the rest is summarized
_MAX_OUTPUT_LINES = 12  # lines of shell output echoed back (head + tail)


def _dim(text: str) -> str:
    return typer.style(text, dim=True)


def format_tokens(n: int) -> str:
    """1234 -> '1.2k', 15600 -> '16k' (compact token counts for the footer)."""
    if n < 1000:
        return str(n)
    if n < 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1000:.0f}k"


def format_seconds(seconds: float) -> str:
    """4.13 -> '4.1s', 75 -> '1m 15s'."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m {rest:02d}s"


# -- shared renderers ---------------------------------------------------------


def render_command_box(command: str, why: str | None, risk: RiskLevel, echo: Echo) -> None:
    """Print the exact shell command in a box, with its reasoning under it.

    The whole panel is coloured by risk (green/yellow/red) so the user can gauge
    a command at a glance before it runs.
    """
    color = _RISK_COLOR.get(risk, typer.colors.GREEN)
    bar = typer.style("│", fg=color)
    echo(typer.style(f"  ┌─ shell · risk: {risk.value.upper()} " + "─" * 10, fg=color, bold=True))
    lines = sanitize_terminal(command).splitlines() or [""]
    for i, line in enumerate(lines):
        prompt = typer.style("$ " if i == 0 else "  ", dim=True)
        echo(f"  {bar} {prompt}" + typer.style(line, bold=True))
    why = (why or "").strip()
    if why:
        first = sanitize_terminal(why.splitlines()[0])
        echo(f"  {bar} " + typer.style(f"why: {first}", fg=color))
    echo(typer.style("  └──", fg=color))


def diff_lines(diff: str, *, indent: str = "  ") -> list[str]:
    """A unified diff as coloured lines (additions green, deletions red), capped."""
    out: list[str] = []
    rows = sanitize_terminal(diff).splitlines()
    for line in rows[:_MAX_DIFF_LINES]:
        if line.startswith(("+++", "---")):
            out.append(_dim(indent + line))
        elif line.startswith("@@"):
            out.append(typer.style(indent + line, fg=typer.colors.CYAN))
        elif line.startswith("+"):
            out.append(typer.style(indent + line, fg=typer.colors.GREEN))
        elif line.startswith("-"):
            out.append(typer.style(indent + line, fg=typer.colors.RED))
        else:
            out.append(_dim(indent + line))
    hidden = len(rows) - _MAX_DIFF_LINES
    if hidden > 0:
        added = sum(1 for r in rows[_MAX_DIFF_LINES:] if r.startswith("+"))
        removed = sum(1 for r in rows[_MAX_DIFF_LINES:] if r.startswith("-"))
        out.append(_dim(f"{indent}… {hidden} more diff lines not shown (+{added} -{removed})"))
    return out


def render_updates(diff: str, echo: Echo) -> None:
    """Print a unified diff under an "updates" label: additions green, deletions red."""
    echo(typer.style("  updates", fg=typer.colors.CYAN, bold=True))
    for line in diff_lines(diff):
        echo(line)


def output_excerpt(output: str, *, limit: int = _MAX_OUTPUT_LINES) -> list[str]:
    """The head and tail of a command's output, with the middle elided."""
    rows = sanitize_terminal(output.rstrip("\n")).splitlines()
    if len(rows) <= limit:
        return rows
    head, tail = limit - limit // 3, limit // 3
    return [*rows[:head], f"… {len(rows) - head - tail} more lines …", *rows[-tail:]]


# -- the live reporter --------------------------------------------------------


class ConsoleReporter:
    """Prints concise, curated step lines as the agent works.

    With the default ``echo`` on an interactive terminal it also renders replies
    as Markdown and shows a spinner while the model is thinking; an injected
    ``echo`` (tests, pipes) gets plain lines and no animation.
    """

    def __init__(self, echo: Echo = typer.echo, *, fancy: bool | None = None) -> None:
        self._echo = echo
        interactive = echo is typer.echo if fancy is None else fancy
        self._markdown = interactive and rich_available()
        self._spinner = Spinner() if interactive and spinner_supported() else None
        self._pending: tuple[str, str] | None = None  # (tool, summary) not yet printed
        self._last_text = ""  # the reply just shown (to avoid repeating it as "why")
        self._denied = False  # the next tool_finished belongs to a denied call
        self._printed_reply = False

    # -- output plumbing ---------------------------------------------------

    def say(self, text: str) -> None:
        """Print a line without fighting the spinner (also used for steering notes)."""
        if self._spinner is not None:
            with self._spinner.paused():
                self._echo(text)
        else:
            self._echo(text)

    def _flush_pending(self) -> None:
        """Print a started-but-unfinished tool line (before something else prints)."""
        if self._pending is not None:
            name, summary = self._pending
            self._pending = None
            self.say(self._tool_line(name, summary))

    def _tool_line(self, name: str, summary: str, detail: str = "", *, ok: bool = True) -> str:
        verb, color = _TOOL_VERB.get(name, (name, typer.colors.BLUE))
        summary = sanitize_terminal(summary)
        line = typer.style(f"  ├─ {verb}", fg=color, bold=True)
        if summary:
            line += typer.style(f" {summary}", dim=True)
        if detail:
            detail = sanitize_terminal(detail.splitlines()[0])
            if ok:
                line += _dim(f"  · {detail}")
            else:
                line += typer.style(f"  ✗ {detail}", fg=typer.colors.RED)
        return line

    # -- reporter hooks ----------------------------------------------------

    def _spin(self, label: str | None) -> None:
        """Show the spinner with ``label``, or hide it (``None``)."""
        if self._spinner is None:
            return
        if label is None:
            self._spinner.stop()
        else:
            self._spinner.label = label
            self._spinner.start()

    def thinking(self, active: bool) -> None:
        self._spin("thinking" if active else None)

    def tool_running(self, name: str) -> None:
        """A (possibly long) tool was authorized and is now executing."""
        if name == "run_shell":
            self._spin("running")
        elif name == "spawn_subagent":
            self._spin("sub-agent working")

    def quiet(self) -> None:
        """Hide the spinner because something else needs the terminal now."""
        self._flush_pending()
        self._spin(None)

    def turn_finished(self) -> None:
        """The turn is over (normally or by Ctrl-C): leave the terminal tidy."""
        self._spin(None)
        self._pending = None
        self._denied = False

    def assistant_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._flush_pending()
        self._last_text = text
        self.say("")  # breathing room between steps
        if self._markdown:
            try:
                print_reply(text, bullet=_BULLET)
                self._printed_reply = True
                return
            except Exception:  # rendering is cosmetic: fall back to plain text
                self._markdown = False
        first, *rest = sanitize_terminal(text).splitlines()
        self.say(typer.style(f"{_BULLET} ", fg=typer.colors.CYAN) + first)
        for line in rest:
            self.say("  " + line)
        self._printed_reply = True

    def tool_started(self, name: str, summary: str) -> None:
        # A live, tree-style line of what the agent is touching — "reading
        # src/app.py", "writing src/new.py" — so multiple files read as a tree.
        if name == "run_shell":
            return  # its boxed command (see `action`) is the display
        self._flush_pending()
        if name in _SLOW_TOOLS:
            self.say(self._tool_line(name, summary))
        else:
            # Held back so start + result print as ONE line when it finishes.
            self._pending = (name, summary)

    def action(self, request: object) -> None:
        """Render a shell command box, or an edit's "updates" diff, before it runs."""
        command = getattr(request, "command", None)
        diff = getattr(request, "diff", None)
        if command is not None:
            self._flush_pending()
            why = getattr(request, "why", None)
            if why and why.strip() == self._last_text:
                why = None  # it was printed as the reply one line up
            for line in _collect(render_command_box, command, why,
                                 getattr(request, "risk", RiskLevel.LOW)):
                self.say(line)
        elif diff:
            self._flush_pending()
            for line in _collect(render_updates, diff):
                self.say(line)

    def awaiting_approval(self, request: object) -> None:
        self._flush_pending()  # the approval prompt is about to draw below it

    def tool_output(self, name: str, output: str, ok: bool) -> None:
        """Echo an excerpt of a command's output so the user can see what it did."""
        self._spin(None)
        bar = _dim("  │")
        for line in output_excerpt(output):
            self.say(f"{bar} " + _dim(line))

    def tool_finished(self, name: str, ok: bool, detail: str) -> None:
        self._spin(None)
        if self._denied:
            self._denied = False  # `permission` already said why
            self._pending = None
            return
        if name == "run_shell":
            mark = typer.style("  ✓", fg=typer.colors.GREEN) if ok else typer.style("  ✗", fg=typer.colors.RED)
            self.say(f"{mark} " + _dim(sanitize_terminal(detail.splitlines()[0] if detail else "")))
            return
        if self._pending is not None and self._pending[0] == name:
            _, summary = self._pending
            self._pending = None
            self.say(self._tool_line(name, summary, detail, ok=ok))
            return
        # The start line was already printed (slow tool / a diff in between).
        mark = typer.style("✓", fg=typer.colors.GREEN) if ok else typer.style("✗", fg=typer.colors.RED)
        self.say(f"  │  {mark} " + _dim(sanitize_terminal(detail.splitlines()[0] if detail else "")))

    def permission(self, tool: str, outcome: str, reason: str | None) -> None:
        if outcome == "denied":
            self._flush_pending()
            self._denied = True
            self.say(typer.style(f"  ⨯ {tool} denied: {reason}", fg=typer.colors.YELLOW))
        elif outcome == "revise":
            self._flush_pending()
            self.say(typer.style("  ↪ not run — redirecting as you asked", fg=typer.colors.YELLOW))

    def info(self, text: str) -> None:
        self._flush_pending()
        self.say(typer.style("  " + sanitize_terminal(text), fg=typer.colors.MAGENTA))


def _collect(render: Callable[..., None], *args: object) -> list[str]:
    """Run an ``echo``-style renderer and return the lines it produced."""
    lines: list[str] = []
    render(*args, lines.append)
    return lines


class CompositeReporter:
    """Fan out reporter hooks to several reporters (e.g. console + voice)."""

    def __init__(self, reporters: list) -> None:
        self._reporters = reporters

    def _each(self, hook: str, *args: object) -> None:
        for reporter in self._reporters:
            fn = getattr(reporter, hook, None)
            if fn is not None:  # optional hooks: not every reporter has them
                fn(*args)

    def assistant_text(self, text: str) -> None:
        self._each("assistant_text", text)

    def tool_started(self, name: str, summary: str) -> None:
        self._each("tool_started", name, summary)

    def action(self, request: object) -> None:
        self._each("action", request)

    def tool_finished(self, name: str, ok: bool, detail: str) -> None:
        self._each("tool_finished", name, ok, detail)

    def permission(self, tool: str, outcome: str, reason: str | None) -> None:
        self._each("permission", tool, outcome, reason)

    def info(self, text: str) -> None:
        self._each("info", text)

    def thinking(self, active: bool) -> None:
        self._each("thinking", active)

    def awaiting_approval(self, request: object) -> None:
        self._each("awaiting_approval", request)

    def tool_output(self, name: str, output: str, ok: bool) -> None:
        self._each("tool_output", name, output, ok)

    def tool_running(self, name: str) -> None:
        self._each("tool_running", name)

    def turn_finished(self) -> None:
        self._each("turn_finished")


# -- end-of-turn summaries ----------------------------------------------------

_STATUS_STYLE = {
    RunStatus.COMPLETE: ("✓", typer.colors.GREEN),
    RunStatus.MAX_STEPS: ("◼", typer.colors.YELLOW),
    RunStatus.ERROR: ("✗", typer.colors.RED),
    RunStatus.ABORTED: ("⏹", typer.colors.YELLOW),
}


def turn_stats(result: RunResult) -> str:
    """'4.1s · 3 steps · 2.4k in / 310 out tokens' (only the parts that are known)."""
    parts = []
    if result.seconds:
        parts.append(format_seconds(result.seconds))
    parts.append(f"{result.steps} step" + ("" if result.steps == 1 else "s"))
    if result.tokens_in or result.tokens_out:
        parts.append(
            f"{format_tokens(result.tokens_in)} in / {format_tokens(result.tokens_out)} out tokens"
        )
    return " · ".join(parts)


def print_turn_footer(result: RunResult, echo: Echo = typer.echo) -> None:
    """One or two quiet lines closing a turn: outcome, stats, files changed.

    A turn that did not complete says *why* — an API error used to surface in
    the chat as nothing more than ``[error]``.
    """
    mark, color = _STATUS_STYLE[result.status]
    echo("")
    if result.status is RunStatus.COMPLETE:
        echo(typer.style(f"  {mark} ", fg=color) + _dim(turn_stats(result)))
    else:
        label = {
            RunStatus.ERROR: "error",
            RunStatus.ABORTED: "stopped",
            RunStatus.MAX_STEPS: "step limit reached",
        }[result.status]
        message = sanitize_terminal(result.final_text.strip())
        first, *rest = message.splitlines() or [""]
        if result.status is RunStatus.ABORTED:
            first = first.replace("Stopped at user request.", "").strip()
            if first in ("Interrupted.", "stop", "stop."):
                first = ""  # "stopped — Interrupted." says the same thing twice
        head = f"  {mark} {label}" + (f" — {first}" if first else "")
        echo(typer.style(head, fg=color, bold=True))
        for line in rest:
            echo(typer.style("    " + line.strip(), fg=color))
        echo(_dim("    " + turn_stats(result)))
    changed = list(getattr(result, "files_changed", ()) or ())
    if changed:
        shown = ", ".join(sanitize_terminal(f) for f in changed[:6])
        more = f" (+{len(changed) - 6} more)" if len(changed) > 6 else ""
        echo(_dim(f"    changed: {shown}{more}"))


def print_finish_summary(result: RunResult) -> None:
    """The closing summary of a one-shot ``jarvis "<task>"`` run.

    The final answer was already printed live by the reporter, so it is not
    repeated here — only the outcome, the stats and what changed.
    """
    print_turn_footer(result, typer.echo)
    state = result.state
    extras = []
    if state.commands_run:
        extras.append(f"{len(state.commands_run)} command" + ("" if len(state.commands_run) == 1 else "s"))
    if state.errors:
        extras.append(f"{len(state.errors)} tool error" + ("" if len(state.errors) == 1 else "s"))
    if state.subagent_summaries:
        extras.append(f"{len(state.subagent_summaries)} sub-agent" + ("" if len(state.subagent_summaries) == 1 else "s"))
    if extras:
        typer.echo(_dim("    " + " · ".join(extras)))
