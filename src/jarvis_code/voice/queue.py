"""The voice subsystem: a cancellable speech queue on its own thread.

Pipeline: a curated status string is enqueued -> shortened/redacted -> handed to
the local engine for playback. Pending and in-progress speech is cancellable
(on approve/deny/interrupt/continue) via :meth:`stop`.

Speech runs on a dedicated worker thread (with its own event loop), not on the
agent's loop. The agent executes tools *synchronously* on its loop, so a worker
task there could not speak until the tools finished: "Awaiting your
authorization" would only be heard after you had already answered the prompt,
and a status line only after the 30-second command it described.

The public methods only ever accept curated status keys / tool names / a command
program name — never raw model output. ``say`` exists for already-curated text
and still runs it through ``shorten`` (which redacts) as a safety net.
"""

from __future__ import annotations

import asyncio
import queue
import threading

from jarvis_code.config import VoiceConfig
from jarvis_code.utils.logging import get_logger

from . import messages
from .engine import SpeakOptions, VoiceEngine

_log = get_logger("voice.queue")

_POLL_SECONDS = 0.05


class VoiceSubsystem:
    def __init__(self, engine: VoiceEngine, voice: VoiceConfig) -> None:
        self.engine = engine
        self.voice = voice
        self.max_chars = voice.output.max_chars
        self.options = SpeakOptions(
            voice=voice.default_voice,
            rate=voice.speech_rate,
            volume=voice.volume,
            pitch=voice.pitch,
            robotic=voice.robotic,
        )
        # Thread-safe: ``say`` is called from the agent's thread, the worker
        # thread consumes.
        self._queue: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._closing = threading.Event()

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._worker is None:
            self._closing.clear()
            self._worker = threading.Thread(
                target=self._run, daemon=True, name="jarvis-voice"
            )
            self._worker.start()

    async def aclose(self) -> None:
        await self.stop()
        worker = self._worker
        if worker is not None:
            self._closing.set()
            # Off the event loop: joining must not freeze the caller's loop.
            await asyncio.to_thread(worker.join, 2.0)
            self._worker = None

    def _run(self) -> None:
        """Worker thread: speak queued lines one at a time on a private loop."""
        loop = asyncio.new_event_loop()
        try:
            while not self._closing.is_set():
                try:
                    text = self._queue.get(timeout=_POLL_SECONDS)
                except queue.Empty:
                    continue
                try:
                    loop.run_until_complete(self.engine.speak(text, self.options))
                except Exception as exc:  # pragma: no cover - never crash on speech
                    _log.warning("speech failed: %s", exc)
                finally:
                    self._queue.task_done()
        finally:
            loop.close()

    # -- enqueue (curated only) -------------------------------------------

    def say(self, text: str) -> None:
        """Enqueue an already-curated string (non-blocking, any thread)."""
        self._queue.put_nowait(messages.shorten(text, self.max_chars))

    async def drain(self) -> None:
        """Wait until everything queued has been spoken (tests/sync points)."""
        while self._queue.unfinished_tasks:
            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        """Cancel pending + in-progress speech immediately."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            self._queue.task_done()
        await self.engine.stop()

    # -- curated event helpers --------------------------------------------

    def announce_tool(self, tool_name: str) -> None:
        phrase = messages.tool_status_phrase(tool_name)
        if phrase:
            self.say(phrase)

    def announce_status(self, key: str) -> None:
        phrase = messages.status_phrase(key)
        if phrase:
            self.say(phrase)

    def announce_permission(self, command: str | None) -> None:
        """Voice an approval request: program name for shell, generic otherwise."""
        if not self.voice.announce_permissions:
            return
        if command:
            self.say(messages.permission_request_phrase(command))
        else:
            self.say(messages.status_phrase("requesting_permission") or "Awaiting your authorization.")

    def announce_subagent(self, *, swarm: bool = False) -> None:
        if not self.voice.announce_subagents:
            return
        if swarm:
            self.say(messages.swarm_phrase(self.voice.swarm_phrase))
        else:
            self.say(messages.status_phrase("launching_subagent") or "Launching a sub-agent.")
