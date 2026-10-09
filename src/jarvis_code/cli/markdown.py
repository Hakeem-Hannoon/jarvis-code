"""Render the model's Markdown replies for the terminal.

Uses ``rich`` when it is importable (it ships with Typer) and stdout is an
interactive terminal; otherwise the caller falls back to plain text. Nothing
here is required: no ``rich``, no problem.
"""

from __future__ import annotations

import sys
from typing import Any

from jarvis_code.utils.text import sanitize_terminal


def rich_available() -> bool:
    """True when Markdown can be rendered richly on this stdout."""
    try:
        if not sys.stdout.isatty():
            return False
        import rich  # noqa: F401
    except Exception:
        return False
    return True


def _left_aligned_markdown() -> Any:
    """A ``rich`` Markdown class whose headings are left-aligned.

    Stock ``rich`` centres every heading (and boxes ``#``), which reads oddly in
    a chat transcript. This keeps headings bold, on the left, in the text flow.
    """
    from rich.markdown import Heading, Markdown

    class _Heading(Heading):
        def __rich_console__(self, console, options):  # type: ignore[override]
            text = self.text
            text.justify = "left"
            text.stylize("bold cyan" if self.tag in ("h1", "h2") else "bold")
            yield text

    class _Markdown(Markdown):
        elements = {**Markdown.elements, "heading_open": _Heading}

    return _Markdown


def print_reply(text: str, *, bullet: str = "●") -> None:
    """Print a reply as Markdown with a coloured bullet in the gutter.

    Lines are rendered without right-padding (and trailing blanks are trimmed),
    so nothing wraps or leaves ragged whitespace if the terminal is resized.
    """
    from rich.console import Console
    from rich.segment import Segment, Segments
    from rich.style import Style

    console = Console(highlight=False)
    options = console.options.update(width=max(20, console.width - 2))
    markdown = _left_aligned_markdown()(sanitize_terminal(text.strip()), code_theme="ansi_dark")
    lines = console.render_lines(markdown, options, pad=False)
    while lines and not "".join(seg.text for seg in lines[-1]).strip():
        lines.pop()  # trailing blank lines

    gutter = Segment(f"{bullet} ", Style(color="cyan"))
    indent = Segment("  ")
    out: list[Segment] = []
    for i, line in enumerate(lines):
        line = list(line)
        # Trim trailing whitespace that carries no background colour.
        while line and not line[-1].text.strip() and not (line[-1].style and line[-1].style.bgcolor):
            line.pop()
        if line and not (line[-1].style and line[-1].style.bgcolor):
            last = line[-1]
            line[-1] = Segment(last.text.rstrip(), last.style)
        out.append(gutter if i == 0 else indent)
        out.extend(line)
        out.append(Segment.line())
    console.print(Segments(out), end="")
