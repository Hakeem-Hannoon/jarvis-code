"""Speech-to-text engine interface + local engines (cloud is opt-in only).

Local engines (faster-whisper, Vosk) run entirely on the machine. A cloud engine
is modeled but is refused unless ``allow_cloud_stt`` is explicitly true. Engines
receive transient audio bytes and return text; they never persist audio.
"""

from __future__ import annotations

import importlib.util
from typing import Protocol, runtime_checkable

from jarvis_code.utils.logging import get_logger

_log = get_logger("input.stt")

LOCAL_ENGINES = {"faster-whisper", "vosk"}
# Example cloud engine names — never used unless allow_cloud_stt is true.
CLOUD_ENGINES = {"cloud", "openai-whisper", "deepgram"}


class CloudSTTNotAllowed(Exception):
    """A cloud STT engine was requested without allow_cloud_stt = true."""


def _have(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


@runtime_checkable
class STTEngine(Protocol):
    name: str
    is_cloud: bool

    async def transcribe(self, audio: bytes) -> str: ...
    async def is_available(self) -> bool: ...


class NoopSTT:
    """No transcription. Used when no local STT is installed; never crashes."""

    name = "noop"
    is_cloud = False

    async def transcribe(self, audio: bytes) -> str:
        return ""

    async def is_available(self) -> bool:
        return False


class FasterWhisperSTT:
    name = "faster-whisper"
    is_cloud = False

    def __init__(self, model: str = "base") -> None:
        self.model = model

    async def is_available(self) -> bool:
        return _have("faster_whisper")

    async def transcribe(self, audio: bytes) -> str:  # pragma: no cover - needs model+audio
        try:
            import io

            from faster_whisper import WhisperModel  # type: ignore

            model = WhisperModel(self.model)
            segments, _ = model.transcribe(io.BytesIO(audio))
            return " ".join(seg.text for seg in segments).strip()
        except Exception as exc:
            _log.warning("faster-whisper failed (%s); no transcript", exc)
            return ""


class VoskSTT:
    name = "vosk"
    is_cloud = False

    def __init__(self, model_dir: str = "") -> None:
        self.model_dir = model_dir

    async def is_available(self) -> bool:
        return _have("vosk")

    async def transcribe(self, audio: bytes) -> str:  # pragma: no cover - needs model+audio
        try:
            import json

            from vosk import KaldiRecognizer, Model  # type: ignore

            model = Model(self.model_dir)
            rec = KaldiRecognizer(model, 16000)
            rec.AcceptWaveform(audio)
            return json.loads(rec.FinalResult()).get("text", "")
        except Exception as exc:
            _log.warning("vosk failed (%s); no transcript", exc)
            return ""


class CloudSTT:  # pragma: no cover - opt-in only, not exercised by default
    name = "cloud"
    is_cloud = True

    async def is_available(self) -> bool:
        return True

    async def transcribe(self, audio: bytes) -> str:
        raise NotImplementedError("Cloud STT integration is intentionally not bundled.")


def build_stt_engine(name: str, *, allow_cloud: bool, model: str = "") -> STTEngine:
    """Construct an STT engine, refusing cloud engines unless explicitly allowed."""
    if name in CLOUD_ENGINES:
        if not allow_cloud:
            raise CloudSTTNotAllowed(
                f"Cloud STT '{name}' requires allow_cloud_stt = true in [voice.input]."
            )
        return CloudSTT()
    if name == "vosk":
        return VoskSTT(model)
    if name == "faster-whisper":
        return FasterWhisperSTT(model or "base")
    return NoopSTT()
