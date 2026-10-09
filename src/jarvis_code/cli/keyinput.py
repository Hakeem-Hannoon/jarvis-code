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


def _read_one_key(fd: int) -> tuple[str, bytes]:  # pragma: no cover - needs a real tty
    """Read one logical keypress in cbreak mode: ``(name, raw bytes)``.

    ``name`` is 'enter' / 'ctrl-c' / 'ctrl-d' / 'space' / 'esc' / 'seq' (an
    escape sequence such as an arrow key) / 'other' (another control key), or
    the typed character itself. The raw bytes let the caller hand a key it does
    not want back to the terminal untouched.
    """
    import select

    ch = os.read(fd, 1)
    if ch in (b"\r", b"\n"):
        return "enter", ch
    if ch == b"\x03":
        return "ctrl-c", ch
    if ch == b"\x04":
        return "ctrl-d", ch
    if ch == b" ":
        return "space", ch
    if ch == b"\x1b":
        # ESC alone, or an escape sequence (arrow keys, Home/End, …).
        ready, _, _ = select.select([fd], [], [], 0.05)
        if ready:
            return "seq", ch + os.read(fd, 8)
        return "esc", ch
    if ch < b" " or ch == b"\x7f":
        return "other", ch
    # A printable character — read the rest of a multi-byte UTF-8 sequence so a
    # leading "é" or "你" is one key, not a dropped byte plus stray garbage.
    lead = ch[0]
    extra = 3 if lead >= 0xF0 else 2 if lead >= 0xE0 else 1 if lead >= 0xC0 else 0
    raw = ch + (os.read(fd, extra) if extra else b"")
    try:
        return raw.decode("utf-8"), raw
    except UnicodeDecodeError:
        return "other", raw


# -- handing the first key back to the line editor ---------------------------
#
# The idle prompt has to look at ONE key to tell "Space = talk" from "start
# typing". Whatever that key was must then reach the line editor, or the first
# character of every message is lost. Three ways, best first:
#
#   push-back  put the bytes back in the terminal's input queue (TIOCSTI) so the
#              editor reads them exactly as typed — works with any readline
#              flavour, and also lets ↑ (history) and other editing keys through.
#   hook       pre-seed GNU readline's buffer from a pre-input hook. macOS's
#              libedit-backed ``readline`` never fires that hook, which is how
#              first characters went missing there.
#   prefix     print the character as part of the prompt and prepend it to the
#              result: always correct, though that one character can't be edited.


def _is_libedit() -> bool:
    try:
        import readline

        return "libedit" in (readline.__doc__ or "")
    except Exception:
        return False


_pushback_works: bool | None = None  # learned on first use


def _drain_pending(fd: int) -> bytes:  # pragma: no cover - needs a real tty
    """Everything already typed and waiting (type-ahead), without blocking."""
    import select

    pending = b""
    while select.select([fd], [], [], 0)[0]:
        chunk = os.read(fd, 1024)
        if not chunk:
            break
        pending += chunk
    return pending


def _push_back(fd: int, data: bytes) -> bool:
    """Queue ``data`` as terminal input. False if this terminal/OS refuses."""
    global _pushback_works
    if _pushback_works is False:
        return False
    try:
        import fcntl
        import termios

        for byte in data:
            fcntl.ioctl(fd, termios.TIOCSTI, bytes([byte]))
    except (OSError, ValueError, AttributeError, ImportError):
        _pushback_works = False  # e.g. Linux with dev.tty.legacy_tiocsti=0
        return False
    _pushback_works = True
    return True


def _input_with_prefix(prompt: str, prefix: str, input_fn: Callable[[str], str]) -> str:
    """Read a line that already starts with ``prefix``; never loses ``prefix``.

    Tries to pre-seed the (editable) readline buffer. If the hook is missing or
    simply never runs — libedit — the prefix is prepended to what was read, so
    the text is intact even if it could not be shown as editable.
    """
    try:
        import readline

        set_hook = readline.set_pre_input_hook
    except Exception:
        return prefix + input_fn(prompt + prefix)

    seeded = False

    def hook() -> None:
        nonlocal seeded
        readline.insert_text(prefix)
        readline.redisplay()
        seeded = readline.get_line_buffer().startswith(prefix)

    set_hook(hook)
    try:
        line = input_fn(prompt)
    finally:
        set_hook(None)
    return line if seeded else prefix + line


def _strategy() -> str:
    """'pushback' | 'hook' | 'prefix' — overridable with JARVIS_KEY_PUSHBACK."""
    forced = os.environ.get("JARVIS_KEY_PUSHBACK", "").strip().lower()
    if forced in ("pushback", "hook", "prefix"):
        return forced
    return "pushback" if _is_libedit() else "hook"


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
    strategy = _strategy()
    pushed = False
    typed = b""
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        key, raw = _read_one_key(fd)
        printable = key not in ("enter", "ctrl-c", "ctrl-d", "space", "esc", "seq", "other")
        if printable and strategy == "pushback":
            # Still in no-echo mode, so the kernel does not echo the re-queued
            # bytes. Type-ahead is drained first: push-back appends to the END
            # of the queue, so "h" must go back in front of an already-typed "i".
            typed = raw + _drain_pending(fd)
            pushed = _push_back(fd, typed)
        elif key in ("seq", "other") and _pushback_works is not False:
            # ↑ for history, Ctrl-R, … belong to the line editor too. Best
            # effort: where push-back is unavailable they are ignored as before.
            typed = raw + _drain_pending(fd)
            pushed = _push_back(fd, typed)
            if not pushed and typed != raw:
                # Could not hand them over: at least don't drop real type-ahead.
                typed = typed[len(raw):]
                key, printable = "typeahead", True
        elif printable:
            typed = raw
    finally:
        # TCSADRAIN (not FLUSH): pending/re-queued input must survive.
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
    if pushed:
        # The editor will read the key(s) itself, exactly as typed.
        return IdleIntent(IdleAction.LINE, input_fn(edit_prompt))
    if not printable:
        return IdleIntent(IdleAction.NONE)

    prefix = typed.decode("utf-8", "ignore")
    first_line, newline, _rest = prefix.replace("\r", "\n").partition("\n")
    if newline:
        return IdleIntent(IdleAction.LINE, first_line)  # a whole line was typed ahead
    if strategy == "hook":
        return IdleIntent(IdleAction.LINE, _input_with_prefix(edit_prompt, prefix, input_fn))
    return IdleIntent(IdleAction.LINE, prefix + input_fn(edit_prompt + prefix))
