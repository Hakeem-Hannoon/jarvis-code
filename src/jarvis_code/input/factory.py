"""STT selection honoring the local-by-default / cloud-opt-in policy."""

from __future__ import annotations

from jarvis_code.config import VoiceInputConfig
from jarvis_code.utils.logging import get_logger

from .mic import MicController, SoundDeviceRecorder
from .stt import CloudSTTNotAllowed, NoopSTT, STTEngine, build_stt_engine

_log = get_logger("input")


def select_stt(config: VoiceInputConfig) -> STTEngine:
    """Build the configured STT engine, falling back to Noop on cloud refusal."""
    try:
        return build_stt_engine(
            config.stt_engine.value, allow_cloud=config.allow_cloud_stt
        )
    except CloudSTTNotAllowed as exc:
        _log.warning("%s Falling back to no STT.", exc)
        return NoopSTT()


def build_mic_controller(config: VoiceInputConfig) -> MicController:
    """Assemble the mic controller for the configured input settings.

    Uses the real sounddevice recorder (which reports itself unavailable when
    the library/hardware is absent, so capture degrades cleanly).
    """
    return MicController(config, select_stt(config), SoundDeviceRecorder())
