"""Ctrl-C handling for a running agent, and terminal-state safety.

Python's default (and asyncio's) SIGINT behaviour is wrong for an agent:

* during a model call the event loop is merely *waiting*, so asyncio cancels the
  main task — which ends the whole chat, and then blocks on the request thread;
* during a tool the main thread is *blocked* (a shell command, an approval
  prompt), where only an exception can break out.

So while a turn runs, Ctrl-C is routed to the runtime: it sets the runtime's
interrupt flag (polled during model calls and at every checkpoint) and, only if
a tool is blocking the main thread, also raises ``KeyboardInterrupt`` there —
which the runtime catches to end *that turn*. A second Ctrl-C while the first is
still being honoured is the escape hatch: it raises unconditionally.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def route_sigint(
    runtime: Any, *, on_idle: Callable[[], None] | None = None
) -> Iterator[None]:
    """Route Ctrl-C to ``runtime`` for the duration of the block.

    ``on_idle`` runs (on the event loop) when Ctrl-C arrives while no turn is
    running — the chat uses it to leave. Without it, an idle Ctrl-C raises
    ``KeyboardInterrupt`` as usual. Must be entered from a running event loop;
    a no-op off the main thread (where signals cannot be installed).
    """
    loop = asyncio.get_running_loop()

    def handler(_signum: int, _frame: object) -> None:
        if getattr(runtime, "busy", False):
            already_asked = runtime.interrupt.is_set()
            runtime.interrupt.set()
            if runtime.in_tool_section or already_asked:
                raise KeyboardInterrupt
            return
        if on_idle is not None:
            loop.call_soon_threadsafe(on_idle)
            return
        raise KeyboardInterrupt

    try:
        previous = signal.signal(signal.SIGINT, handler)
    except ValueError:  # not the main thread: leave signals alone
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def save_terminal_state() -> object | None:
    """Snapshot the terminal's settings (None when stdin is not a TTY)."""
    try:
        import termios

        if sys.stdin.isatty():
            return termios.tcgetattr(sys.stdin.fileno())
    except Exception:  # non-POSIX, closed stdin, …
        pass
    return None


def restore_terminal_state(state: object | None) -> None:
    """Put the terminal back the way :func:`save_terminal_state` found it.

    Leaving the chat with Ctrl-C abandons a reader thread that may hold the
    terminal in raw/no-echo mode; without this the user's shell would be left
    not echoing what they type.
    """
    if state is None:
        return
    try:
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, state)  # type: ignore[arg-type]
    except Exception:  # best effort
        pass
