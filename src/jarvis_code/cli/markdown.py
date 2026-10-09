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
    """Print a reply as Markdown with a coloured bullet in the gutter."""
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = Console(highlight=False)
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=1, no_wrap=True)
    grid.add_column(ratio=1)
    markdown = _left_aligned_markdown()(sanitize_terminal(text.strip()), code_theme="ansi_dark")
    grid.add_row(Text(bullet, style="cyan"), markdown)
    console.print(grid)
