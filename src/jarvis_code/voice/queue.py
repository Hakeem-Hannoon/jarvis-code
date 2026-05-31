"""The voice subsystem: a cancellable async speech queue.

Pipeline: a curated status string is enqueued -> shortened/redacted -> handed to
the local engine for async playback. Pending and in-progress speech is
cancellable (on approve/deny/interrupt/continue) via :meth:`stop`.

The public methods only ever accept curated status keys / tool names / a command
program name — never raw model output. ``say`` exists for already-curated text
and still runs it through ``shorten`` (which redacts) as a safety net.
"""

from __future__ import annotations

import asyncio

from jarvis_code.config import VoiceConfig
from jarvis_code.utils.logging import get_logger

from . import messages
from .engine import SpeakOptions, VoiceEngine

_log = get_logger("voice.queue")


class VoiceSubsystem:
    def __init__(self, engine: VoiceEngine, voice: VoiceConfig) -> None:
        self.engine = engine
        self.voice = voice
        self.max_chars = voice.output.max_chars
        self.options = SpeakOptions(
            voice=voice.default_voice, rate=voice.speech_rate, volume=voice.volume
        )
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def aclose(self) -> None:
        await self.stop()
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def _run(self) -> None:
        while True:
            text = await self._queue.get()
            try:
                await self.engine.speak(text, self.options)
            except Exception as exc:  # pragma: no cover - never crash on speech
                _log.warning("speech failed: %s", exc)
            finally:
                self._queue.task_done()

    # -- enqueue (curated only) -------------------------------------------

    def say(self, text: str) -> None:
        """Enqueue an already-curated string (non-blocking)."""
        self._queue.put_nowait(messages.shorten(text, self.max_chars))

    async def drain(self) -> None:
        """Wait until everything queued has been spoken (tests/sync points)."""
        await self._queue.join()

    async def stop(self) -> None:
        """Cancel pending + in-progress speech immediately."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:  # pragma: no cover
                break
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
        if self.voice.announce_permissions:
            self.say(messages.permission_request_phrase(command))

    def announce_subagent(self, *, swarm: bool = False) -> None:
        if not self.voice.announce_subagents:
            return
        if swarm:
            self.say(messages.swarm_phrase(self.voice.swarm_phrase))
        else:
            self.say(messages.status_phrase("launching_subagent") or "Launching a sub-agent.")
