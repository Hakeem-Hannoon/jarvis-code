"""A small arrow-key picker with type-to-filter.

Used by ``/model``, ``/provider``, ``/effort`` and ``/mode``::

      model · anthropic            ↑/↓ move · type to filter · Enter · Esc
      ❯ claude-opus-4-8      current
        claude-sonnet-5
        claude-haiku-4-5
        gpt-5                openai
      3 more · filter: _

Typing narrows the list; with ``allow_custom`` whatever was typed can be chosen
as-is (an Azure deployment name the API can't enumerate). Esc clears the filter,
then cancels. Off a real terminal (pipes, tests) it degrades to a numbered list
and one typed answer.

Every label is sanitized: model ids come from a provider's API response.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from jarvis_code.utils.text import sanitize_terminal

_MAX_ROWS = 10
_CYAN_BOLD, _DIM, _RESET = "\x1b[1;36m", "\x1b[2m", "\x1b[0m"


@dataclass(frozen=True)
class Option:
    value: str
    label: str = ""  # shown text (defaults to ``value``)
    hint: str = ""  # dim note on the right: "current", a provider name, …

    @property
    def text(self) -> str:
        return sanitize_terminal(self.label or self.value)


def filter_options(options: Sequence[Option], query: str) -> list[Option]:
    """Options matching every whitespace-separated term of ``query`` (case-insensitive)."""
    terms = query.lower().split()
    if not terms:
        return list(options)
    return [
        o for o in options
        if all(t in f"{o.text} {o.hint} {o.value}".lower() for t in terms)
    ]


def _with_custom(matches: list[Option], query: str, allow_custom: bool) -> list[Option]:
    """Append a "use what I typed" entry when it isn't already an option."""
    typed = query.strip()
    if allow_custom and typed and not any(typed in (o.value, o.text) for o in matches):
        matches = [*matches, Option(typed, f'use "{sanitize_terminal(typed)}"', "as typed")]
    return matches


def _interactive(input_fn: Callable[[str], str]) -> bool:
    if input_fn is not input:
        return False  # an injected reader (tests) gets the typed fallback
    try:
        import termios  # noqa: F401

        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:  # non-POSIX, closed stdio
        return False


def pick(
    title: str,
    options: Sequence[Option],
    *,
    current: str | None = None,
    allow_custom: bool = False,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    interactive: bool | None = None,
) -> str | None:
    """Let the user choose one option; returns its ``value`` or None if cancelled."""
    if not options and not allow_custom:
        return None
    use_keys = _interactive(input_fn) if interactive is None else interactive
    if use_keys:
        try:
            return _pick_with_keys(title, options, current, allow_custom)
        except Exception:  # the terminal refused raw mode: fall back to typing
            pass
    return _pick_typed(title, options, current, allow_custom, input_fn, output_fn)


# -- typed fallback -----------------------------------------------------------


def _pick_typed(title, options, current, allow_custom, input_fn, output_fn) -> str | None:
    output_fn(f"  {title}")
    shown = list(options)[:40]
    for i, option in enumerate(shown, start=1):
        mark = "*" if option.value == current else " "
        hint = f"  ({option.hint})" if option.hint else ""
        output_fn(f"  {mark}{i:>2}) {option.text}{hint}")
    if len(options) > len(shown):
        output_fn(f"      … {len(options) - len(shown)} more — type a name")
    try:
        answer = input_fn("  number or name (blank cancels): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not answer:
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(shown):
        return shown[int(answer) - 1].value
    for option in options:
        if answer in (option.value, option.text):
            return option.value
    matches = filter_options(options, answer)
    if len(matches) == 1:
        return matches[0].value
    return answer if allow_custom else None


# -- interactive --------------------------------------------------------------


def _read_key(fd: int) -> str:
    """One logical key: 'up', 'down', 'enter', 'esc', 'backspace', 'cancel', or a char."""
    import select

    first = os.read(fd, 1)
    if first in (b"\r", b"\n"):
        return "enter"
    if first in (b"\x03", b"\x04"):  # Ctrl-C / Ctrl-D (ISIG is off while picking)
        return "cancel"
    if first in (b"\x7f", b"\x08"):
        return "backspace"
    if first == b"\x15":  # Ctrl-U
        return "clear"
    if first == b"\x10":  # Ctrl-P
        return "up"
    if first == b"\x0e":  # Ctrl-N
        return "down"
    if first == b"\x1b":
        ready, _, _ = select.select([fd], [], [], 0.03)
        if not ready:
            return "esc"
        seq = os.read(fd, 8).decode("ascii", "ignore")
        return {"[A": "up", "[B": "down", "OA": "up", "OB": "down",
                "[5~": "pageup", "[6~": "pagedown"}.get(seq, "other")
    if first < b" ":
        return "other"
    # A (possibly multi-byte) printable character.
    lead = first[0]
    extra = 3 if lead >= 0xF0 else 2 if lead >= 0xE0 else 1 if lead >= 0xC0 else 0
    data = first + (os.read(fd, extra) if extra else b"")
    return data.decode("utf-8", "ignore") or "other"


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def _frame(title, matches, idx, top, rows, query, current, width) -> list[str]:
    """The lines of one picker frame (already styled, each narrower than the terminal)."""
    help_text = "↑/↓ move · type to filter · Enter · Esc"
    head = _clip(f"  {sanitize_terminal(title)}", width)
    pad = width - len(head) - len(help_text)
    lines = [f"\x1b[1m{head}{_RESET}" + (" " * pad + f"{_DIM}{help_text}{_RESET}" if pad >= 2 else "")]

    label_width = max((len(o.text) for o in matches), default=0)
    label_width = min(label_width, max(10, width - 24))
    for i in range(top, min(top + rows, len(matches))):
        option = matches[i]
        hint = "current" if option.value == current and not option.hint else option.hint
        plain = f"{_clip(option.text, label_width):<{label_width}}"
        tail = _clip(f"  {sanitize_terminal(hint)}", max(0, width - label_width - 4)) if hint else ""
        if i == idx:
            lines.append(f"  {_CYAN_BOLD}❯ {plain}{_RESET}{_DIM}{tail}{_RESET}")
        else:
            lines.append(f"    {plain}{_DIM}{tail}{_RESET}")
    if not matches:
        lines.append(f"    {_DIM}no matches{_RESET}")

    hidden = len(matches) - min(rows, len(matches))
    status = []
    if hidden > 0:
        status.append(f"{idx + 1} of {len(matches)}")
    if query:
        status.append(f"filter: {sanitize_terminal(query)}")
    lines.append(f"  {_DIM}{_clip(' · '.join(status), width - 2)}{_RESET}" if status else "")
    return lines


def _pick_with_keys(title, options, current, allow_custom) -> str | None:  # pragma: no cover - needs a tty
    import termios
    import tty

    fd = sys.stdin.fileno()
    out = sys.stdout
    size = shutil.get_terminal_size((80, 24))
    width = max(30, size.columns - 1)
    rows = max(3, min(_MAX_ROWS, size.lines - 5))

    query = ""
    matches = _with_custom(filter_options(options, query), query, allow_custom)
    idx = next((i for i, o in enumerate(matches) if o.value == current), 0)
    top = 0
    drawn = 0

    def draw() -> None:
        nonlocal drawn, top
        top = min(max(top, idx - rows + 1), idx) if matches else 0
        top = max(0, min(top, max(0, len(matches) - rows)))
        if drawn:
            out.write(f"\x1b[{drawn}A")
        out.write("\r\x1b[J")
        frame = _frame(title, matches, idx, top, rows, query, current, width)
        out.write("\n".join(frame) + "\n")
        out.flush()
        drawn = len(frame)

    def erase() -> None:
        if drawn:
            out.write(f"\x1b[{drawn}A\r\x1b[J")
            out.flush()

    old = termios.tcgetattr(fd)
    chosen: str | None = None
    try:
        tty.setcbreak(fd)
        attrs = termios.tcgetattr(fd)
        attrs[3] &= ~termios.ISIG  # deliver Ctrl-C as a key so it just cancels
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
        out.write("\x1b[?25l")  # hide the cursor
        draw()
        while True:
            key = _read_key(fd)
            if key == "enter":
                if matches:
                    chosen = matches[idx].value
                break
            if key == "cancel":
                break
            if key == "esc":
                if not query:
                    break
                query = ""  # first Esc clears the filter, the next one cancels
            elif key == "up":
                idx = (idx - 1) % len(matches) if matches else 0
                draw()
                continue
            elif key == "down":
                idx = (idx + 1) % len(matches) if matches else 0
                draw()
                continue
            elif key in ("pageup", "pagedown"):
                step = -rows if key == "pageup" else rows
                idx = max(0, min(len(matches) - 1, idx + step)) if matches else 0
                draw()
                continue
            elif key == "backspace":
                query = query[:-1]
            elif key == "clear":
                query = ""
            elif key == "other":
                continue
            else:
                query += key
            matches = _with_custom(filter_options(options, query), query, allow_custom)
            idx, top = 0, 0
            draw()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        erase()
        out.write("\x1b[?25h")
        out.flush()
    return chosen
