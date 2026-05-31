"""STT selection honoring the local-by-default / cloud-opt-in policy."""

from __future__ import annotations

from jarvis_code.config import VoiceInputConfig
from jarvis_code.utils.logging import get_logger

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
