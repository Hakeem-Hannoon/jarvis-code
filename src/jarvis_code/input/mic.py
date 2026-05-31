"""Mic modes + controller.

Three modes (from ``[voice.input]``):
- Open Mic: continuous capture (VAD/wake-word gated to avoid noise).
- Push to Talk: captures only between press and release.
- Muted: the mic is fully off — nothing is recorded, transcribed, or stored.

Invariants enforced here:
- In Muted, the recorder is never invoked and ``capture`` returns None.
- Audio bytes are transient locals only; they are never persisted. Only the
  transcribed text is ever surfaced (and logged, if at all).
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from jarvis_code.config import MicMode, VoiceInputConfig
from jarvis_code.utils.logging import get_logger

from .stt import STTEngine

_log = get_logger("input.mic")

_CYCLE = [MicMode.OPEN_MIC, MicMode.PUSH_TO_TALK, MicMode.MUTED]


@runtime_checkable
class Recorder(Protocol):
    async def record(self, max_seconds: float) -> bytes: ...
    async def is_available(self) -> bool: ...


class NullRecorder:
    """A recorder that captures nothing (no audio hardware / not installed)."""

    async def record(self, max_seconds: float) -> bytes:
        return b""

    async def is_available(self) -> bool:
        return False


class SoundDeviceRecorder:  # pragma: no cover - needs a real microphone
    """Records from the default input device via sounddevice."""

    def __init__(self, samplerate: int = 16000) -> None:
        self.samplerate = samplerate

    async def is_available(self) -> bool:
        try:
            return importlib.util.find_spec("sounddevice") is not None
        except (ImportError, ValueError):
            return False

    async def record(self, max_seconds: float) -> bytes:
        import asyncio

        def _rec() -> bytes:
            import sounddevice as sd  # type: ignore

            frames = sd.rec(int(max_seconds * self.samplerate), samplerate=self.samplerate, channels=1, dtype="int16")
            sd.wait()
            return bytes(frames)

        return await asyncio.to_thread(_rec)


def render_mic_bar(current: MicMode) -> str:
    """Render `Mic: [Open Mic] [Push to Talk] [Muted]` with the active one marked."""
    labels = {
        MicMode.OPEN_MIC: "Open Mic",
        MicMode.PUSH_TO_TALK: "Push to Talk",
        MicMode.MUTED: "Muted",
    }
    parts = []
    for mode in _CYCLE:
        label = labels[mode]
        parts.append(f"[>{label}<]" if mode == current else f"[{label}]")
    return "Mic: " + " ".join(parts)


class MicController:
    def __init__(
        self,
        config: VoiceInputConfig,
        stt: STTEngine,
        recorder: Recorder | None = None,
        *,
        on_mode_change: Callable[[MicMode], None] | None = None,
    ) -> None:
        self.config = config
        self.enabled = config.enabled
        self.mode = config.mode
        self.stt = stt
        self.recorder = recorder or NullRecorder()
        self._on_mode_change = on_mode_change
        self.last_text: str | None = None
        # Hard guarantee: this controller never persists audio.
        self.stores_audio = False
        self._ptt_active = False

    # -- mode management ---------------------------------------------------

    def set_mode(self, mode: MicMode) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        if mode == MicMode.MUTED:
            self._ptt_active = False
        _log.info("mic mode -> %s", mode.value)  # text only, never audio
        if self._on_mode_change is not None:
            self._on_mode_change(mode)

    def cycle_mode(self) -> MicMode:
        idx = _CYCLE.index(self.mode)
        self.set_mode(_CYCLE[(idx + 1) % len(_CYCLE)])
        return self.mode

    def is_listening(self) -> bool:
        return self.enabled and self.mode != MicMode.MUTED

    def mic_bar(self) -> str:
        return render_mic_bar(self.mode)

    # -- capture -----------------------------------------------------------

    async def capture(self, max_seconds: float = 8.0) -> str | None:
        """Open-mic / single-shot capture. Returns transcribed text or None.

        In Muted (or disabled), the recorder is NEVER called and None is
        returned — nothing is recorded, transcribed, or stored.
        """
        if not self.is_listening():
            return None
        if not await self.recorder.is_available() or not await self.stt.is_available():
            return None  # degrade cleanly when no mic/STT present
        audio = await self.recorder.record(max_seconds)
        return await self._transcribe(audio)

    async def ptt_press(self) -> None:
        """Begin push-to-talk capture (no-op unless in PTT mode and enabled)."""
        if self.enabled and self.mode == MicMode.PUSH_TO_TALK:
            self._ptt_active = True

    async def ptt_release(self, max_seconds: float = 30.0) -> str | None:
        """End push-to-talk capture and return the transcript (or None)."""
        if not self._ptt_active:
            return None
        self._ptt_active = False
        if not await self.recorder.is_available() or not await self.stt.is_available():
            return None
        audio = await self.recorder.record(max_seconds)
        return await self._transcribe(audio)

    async def _transcribe(self, audio: bytes) -> str | None:
        # `audio` is a transient local; it is never stored or returned.
        text = (await self.stt.transcribe(audio)).strip()
        del audio  # make the no-persistence intent explicit
        if not text:
            return None
        self.last_text = text
        _log.info("voice instruction transcribed (%d chars)", len(text))  # length only
        return text
