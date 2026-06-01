"""Local/offline voice output: engines, curated messages, queue, reporter."""

from __future__ import annotations

from .engine import SpeakOptions, VoiceEngine
from .factory import engine_available, select_engine
from .noop import NoopVoiceEngine
from .piper import PiperVoiceEngine
from .queue import VoiceSubsystem
from .reporter import VoiceReporter
from .system import SystemVoiceEngine, say_argv, system_tts_available

__all__ = [
    "SpeakOptions",
    "VoiceEngine",
    "engine_available",
    "select_engine",
    "NoopVoiceEngine",
    "PiperVoiceEngine",
    "SystemVoiceEngine",
    "say_argv",
    "system_tts_available",
    "VoiceSubsystem",
    "VoiceReporter",
]
