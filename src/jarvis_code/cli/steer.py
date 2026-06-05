"""Live steering: talk to the agent *while* it works.

While a turn is running, a background reader submits each line you type to the
runtime's :class:`InstructionQueue`. The agent folds non-permission instructions
in at its next safe checkpoint (after the current small step), and a permission
answer ("yes"/"no"/free text) is routed to an active approval prompt. Reading
pauses while an approval menu owns the terminal, so the two never fight over
stdin.

Only the routing core (``_pump_once``) and the prompter wrapper are unit-tested;
the select-based stdin loop needs a real TTY and is kept deliberately thin.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable

from jarvis_code.events import InstructionKind, InstructionQueue
from jarvis_code.events.prompter import map_text_to_response
from jarvis_code.permissions import PermissionRequest, PermissionResponse, Prompter

_EOF = ""  # what a reader returns at end-of-input


def _default_readline(timeout: float = 0.1) -> str | None:  # pragma: no cover - needs a tty
    """A line from stdin, ``None`` on timeout, or ``""`` at EOF. Real-TTY only."""
    import select

    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
    except (OSError, ValueError):
        return _EOF
    if not ready:
        return None
    return sys.stdin.readline()


class SteerSession:
    """Background reader that turns typed lines into live steering instructions."""

    def __init__(
        self,
        instructions: InstructionQueue,
        *,
        echo: Callable[[str], None] = lambda _s: None,
        readline: Callable[[], str | None] | None = None,
    ) -> None:
        self.instructions = instructions
        self._echo = echo
        self._readline = readline or _default_readline
        # Set while an approval menu owns stdin, so the reader stands down.
        self.prompt_active = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle (real interactive use) ----------------------------------

    def start(self) -> None:
        """Begin reading in a daemon thread (no-op off a real TTY)."""
        if self._thread is not None:
            return
        try:
            if not sys.stdin.isatty():  # pragma: no cover - only steer on a real tty
                return
        except (ValueError, OSError):  # pragma: no cover
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=0.5)
        self._thread = None

    def _run(self) -> None:  # pragma: no cover - needs a real tty + thread
        while not self._stop.is_set():
            if not self._pump_once():
                return

    # -- testable core -----------------------------------------------------

    def _pump_once(self) -> bool:
        """One read→classify→submit cycle. Returns False to stop (EOF)."""
        if self.prompt_active.is_set():
            time.sleep(0.02)  # an approval menu owns stdin — stand down
            return True
        line = self._readline()
        if line is None:  # nothing typed within the poll window
            return True
        if line == _EOF:  # end of input
            return False
        text = line.strip()
        if not text:
            return True
        instr = self.instructions.submit(text, source="text")
        self._announce(instr.kind)
        return True

    def _announce(self, kind: InstructionKind) -> None:
        if kind == InstructionKind.HARD_INTERRUPT:
            self._echo("⏹  stopping after the current step…")
        elif kind == InstructionKind.SOFT_INTERRUPT:
            self._echo("↪ noted — I'll adjust after this step.")
        elif kind == InstructionKind.PERMISSION_RESPONSE:
            self._echo("↪ answer queued.")
        else:
            self._echo("↪ steering queued — folding in after the current step.")


class PausingQueuePrompter:
    """Permission prompter for a live session.

    If the user already queued an answer, use it. Otherwise pause the steering
    reader (so the approval menu owns stdin) and defer to the inner prompter.
    """

    def __init__(
        self,
        queue: InstructionQueue,
        inner: Prompter,
        prompt_active: threading.Event,
    ) -> None:
        self._queue = queue
        self._inner = inner
        self._prompt_active = prompt_active

    def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        instr = self._queue.pop_next()
        if instr is not None:
            return map_text_to_response(instr.text)
        self._prompt_active.set()
        try:
            return self._inner.request_permission(request)
        finally:
            self._prompt_active.clear()
