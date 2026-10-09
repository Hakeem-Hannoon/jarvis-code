"""A small "thinking…" spinner that coexists with typing.

While the model is working the user may be typing a steering line. A naive
spinner redraws its whole line every tick, wiping what they typed off the
screen. This one draws itself once and then only repaints its own fixed-width
cell using save/restore-cursor, so text typed after it stays put.

It is purely cosmetic and switches itself off whenever the output isn't an
interactive terminal (pipes, CI, ``TERM=dumb``) or ``JARVIS_NO_SPINNER`` is set.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import TextIO

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_INTERVAL = 0.1
_CYAN, _DIM, _RESET = "\x1b[36m", "\x1b[2m", "\x1b[0m"
_SAVE, _RESTORE, _CLEAR_LINE = "\x1b7", "\x1b8", "\r\x1b[2K"


def spinner_supported(stream: TextIO | None = None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("JARVIS_NO_SPINNER") or os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except Exception:  # pragma: no cover - unusual stdio
        return False


class Spinner:
    """Animated status cell. ``start``/``stop`` are idempotent and thread-safe.

    Anything else that writes to the terminal while it runs should do so inside
    :meth:`paused`, which clears the cell first and lets it redraw afterwards.
    """

    def __init__(self, label: str = "thinking", *, stream: TextIO | None = None) -> None:
        self.label = label
        self._stream = stream or sys.stdout
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0
        self._fresh = True  # next draw must be a full one (cursor is elsewhere)

    @property
    def active(self) -> bool:
        return self._thread is not None

    def _cell(self, frame: str) -> str:
        seconds = int(time.monotonic() - self._started)
        # Fixed visible width so the repaint never spills onto typed text.
        text = f"{self.label}… {seconds:>3}s"
        return f"  {_CYAN}{frame}{_RESET} {_DIM}{text}{_RESET} "

    def _draw(self, frame: str) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            if self._fresh:
                self._stream.write("\r" + self._cell(frame))
                self._fresh = False
            else:
                self._stream.write(_SAVE + "\r" + self._cell(frame) + _RESTORE)
            self._stream.flush()

    def _run(self) -> None:
        i = 0
        while not self._stop.wait(_INTERVAL):
            i += 1
            self._draw(_FRAMES[i % len(_FRAMES)])

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._stop.clear()
            self._started = time.monotonic()
            self._fresh = True
            self._draw(_FRAMES[0])
            self._thread = threading.Thread(target=self._run, daemon=True, name="jarvis-spinner")
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._stop.set()
            self._thread = None
            self._stream.write(_CLEAR_LINE)
            self._stream.flush()
        thread.join(timeout=1.0)

    def paused(self) -> "_Paused":
        """Context manager: clear the cell, let the caller print, then redraw."""
        return _Paused(self)


class _Paused:
    def __init__(self, spinner: Spinner) -> None:
        self._spinner = spinner

    def __enter__(self) -> None:
        s = self._spinner
        s._lock.acquire()
        if s.active:
            s._stream.write(_CLEAR_LINE)
            s._stream.flush()

    def __exit__(self, *_exc: object) -> None:
        s = self._spinner
        s._fresh = True  # the cursor moved: repaint from column 0 next tick
        s._lock.release()
