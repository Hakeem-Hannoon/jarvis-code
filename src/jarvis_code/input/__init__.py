"""Voice input: local STT engines, mic modes, and the mic controller."""

from __future__ import annotations

from .factory import build_mic_controller, select_stt
from .mic import (
    MicController,
    NullRecorder,
    Recorder,
    SoundDeviceRecorder,
    render_mic_bar,
)
from .stt import (
    CLOUD_ENGINES,
    LOCAL_ENGINES,
    CloudSTT,
    CloudSTTNotAllowed,
    FasterWhisperSTT,
    NoopSTT,
    STTEngine,
    VoskSTT,
    build_stt_engine,
)

__all__ = [
    "select_stt",
    "build_mic_controller",
    "MicController",
    "NullRecorder",
    "Recorder",
    "SoundDeviceRecorder",
    "render_mic_bar",
    "CLOUD_ENGINES",
    "LOCAL_ENGINES",
    "CloudSTT",
    "CloudSTTNotAllowed",
    "FasterWhisperSTT",
    "NoopSTT",
    "STTEngine",
    "VoskSTT",
    "build_stt_engine",
]
