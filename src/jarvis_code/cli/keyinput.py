"""Single-key idle input for the chat prompt.

When the mic is on and stdin is a real TTY, the idle prompt reads ONE keypress in
cbreak mode so a bare **Space** starts a voice capture (no need to type ``/talk``).
Any other key falls straight through to ordinary readline line editing — so
typing, history, and slash commands are unchanged. Everything degrades to a plain
``input()`` line when there is no TTY (pipes, tests) or termios is unavailable.

Honest limitation: a terminal delivers key *presses*, never *releases*, so true
press-and-hold is not possible here. Space is tap-to-start; Enter stops the
recording (handled by the caller).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class IdleAction(str, Enum):
    LINE = "line"  # the user typed a line of text (``text`` holds it)
    TALK = "talk"  # start a voice capture
    NONE = "none"  # ignore this keypress and re-prompt


@dataclass
class IdleIntent:
    action: IdleAction
    text: str = ""


def supports_raw_keys() -> bool:
    """True only when we can safely read single keys (a POSIX TTY with termios)."""
    try:
        import termios  # noqa: F401
        import tty  # noqa: F401
    except Exception:  # pragma: no cover - non-POSIX (e.g. Windows)
        return False
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:  # pragma: no cover - unusual stdio
        return False


def _read_one_key(fd: int) -> str:  # pragma: no cover - needs a real tty
    """Read one logical keypress in cbreak mode."""
    import select

    ch = os.read(fd, 1)
    if ch in (b"\r", b"\n"):
        return "enter"
    if ch == b"\x03":
        return "ctrl-c"
    if ch == b"\x04":
        return "ctrl-d"
    if ch == b" ":
        return "space"
    if ch == b"\x1b":
        # ESC alone, or an escape sequence (arrow keys) we ignore at idle.
        ready, _, _ = select.select([fd], [], [], 0.05)
        if ready:
            os.read(fd, 2)
            return "seq"
        return "esc"
    try:
        return ch.decode("utf-8")
    except UnicodeDecodeError:
        return "other"


def _input_with_prefix(
    prompt: str, prefix: str, input_fn: Callable[[str], str]
) -> str:  # pragma: no cover - needs readline + tty
    """Read a line with ``prefix`` pre-seeded into the (editable) readline buffer."""
    try:
        import readline
    except Exception:
        return prefix + input_fn(prompt)

    def hook() -> None:
        readline.insert_text(prefix)
        readline.redisplay()

    readline.set_pre_input_hook(hook)
    try:
        return input_fn(prompt)
    finally:
        readline.set_pre_input_hook(None)


def read_idle_intent(
    plain_prompt: str,
    edit_prompt: str,
    *,
    input_fn: Callable[[str], str] = input,
) -> IdleIntent:
    """One idle read.

    ``Space`` → :attr:`IdleAction.TALK`; a typed character begins a normal edited
    line (returned as :attr:`IdleAction.LINE`). ``plain_prompt`` is shown for the
    raw key read (no readline width markers); ``edit_prompt`` is the readline
    prompt used once the user starts typing. Raises ``EOFError`` on Ctrl-C/Ctrl-D
    so the caller leaves the chat. With no TTY this is just a plain line read.
    """
    if not supports_raw_keys():
        return IdleIntent(IdleAction.LINE, input_fn(edit_prompt))

    import termios  # pragma: no cover - needs a real tty
    import tty  # pragma: no cover

    fd = sys.stdin.fileno()  # pragma: no cover - from here down needs a real tty
    sys.stdout.write(plain_prompt)
    sys.stdout.flush()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        key = _read_one_key(fd)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    if key in ("ctrl-c", "ctrl-d"):
        sys.stdout.write("\n")
        raise EOFError
    # Clear the plain prompt line before anything else writes.
    sys.stdout.write("\r\x1b[2K")
    sys.stdout.flush()
    if key == "space":
        return IdleIntent(IdleAction.TALK)
    if key == "enter":
        return IdleIntent(IdleAction.LINE, "")
    if key in ("esc", "seq", "other"):
        return IdleIntent(IdleAction.NONE)
    # A printable character: continue it as a normally-edited line.
    return IdleIntent(IdleAction.LINE, _input_with_prefix(edit_prompt, key, input_fn))
