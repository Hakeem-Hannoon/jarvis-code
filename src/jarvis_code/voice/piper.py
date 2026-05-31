"""PiperVoiceEngine — local neural TTS via Piper (lazy, optional).

Requires the ``piper-tts`` package, the ``sounddevice`` playback library, and a
downloaded ``.onnx`` voice model on disk (path from config). Everything is local;
no audio or text leaves the machine. Degrades cleanly when anything is missing.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

from jarvis_code.utils.logging import get_logger

from .engine import SpeakOptions

_log = get_logger("voice.piper")


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


class PiperVoiceEngine:
    name = "piper"

    def __init__(self, model_path: str = "", config_path: str = "") -> None:
        self.model_path = model_path
        self.config_path = config_path
        self._stop = False

    def _ready(self) -> bool:
        return (
            _module_available("piper")
            and _module_available("sounddevice")
            and bool(self.model_path)
            and Path(self.model_path).expanduser().is_file()
        )

    async def is_available(self) -> bool:
        return self._ready()

    async def speak(self, text: str, options: SpeakOptions | None = None) -> None:
        if not self._ready():
            _log.debug("piper not ready (missing package or model); skipping speech")
            return
        self._stop = False
        await asyncio.to_thread(self._speak_blocking, text, options or SpeakOptions())

    def _speak_blocking(self, text: str, options: SpeakOptions) -> None:  # pragma: no cover
        try:
            import sounddevice as sd  # type: ignore
            from piper.voice import PiperVoice  # type: ignore

            voice = PiperVoice.load(self.model_path, config_path=self.config_path or None)
            for chunk in voice.synthesize_stream_raw(text):
                if self._stop:
                    break
                import numpy as np  # type: ignore

                audio = np.frombuffer(chunk, dtype="int16")
                sd.play(audio, samplerate=voice.config.sample_rate)
                sd.wait()
        except Exception as exc:  # never crash the agent over speech
            _log.warning("piper TTS failed (%s); continuing silently", exc)

    async def stop(self) -> None:  # pragma: no cover - best effort
        self._stop = True
        try:
            import sounddevice as sd  # type: ignore

            sd.stop()
        except Exception:
            pass
