"""SystemVoiceEngine — OS TTS via pyttsx3 (lazy, optional)."""

from __future__ import annotations

import asyncio
import importlib.util

from jarvis_code.utils.logging import get_logger

from .engine import SpeakOptions

_log = get_logger("voice.system")


def _pyttsx3_available() -> bool:
    try:
        return importlib.util.find_spec("pyttsx3") is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


class SystemVoiceEngine:
    name = "system"

    def __init__(self) -> None:
        self._stop = False

    async def is_available(self) -> bool:
        return _pyttsx3_available()

    async def speak(self, text: str, options: SpeakOptions | None = None) -> None:
        if not _pyttsx3_available():
            _log.debug("pyttsx3 not installed; skipping speech")
            return
        self._stop = False
        # pyttsx3 is blocking; run it off the event loop so playback is async.
        await asyncio.to_thread(self._speak_blocking, text, options or SpeakOptions())

    def _speak_blocking(self, text: str, options: SpeakOptions) -> None:  # pragma: no cover
        try:
            import pyttsx3  # type: ignore

            engine = pyttsx3.init()
            engine.setProperty("rate", int(175 * options.rate))
            engine.setProperty("volume", options.volume)
            engine.say(text)
            engine.runAndWait()
        except Exception as exc:  # never crash the agent over speech
            _log.warning("system TTS failed (%s); continuing silently", exc)

    async def stop(self) -> None:  # pragma: no cover - best effort
        self._stop = True
        try:
            import pyttsx3  # type: ignore

            pyttsx3.init().stop()
        except Exception:
            pass
