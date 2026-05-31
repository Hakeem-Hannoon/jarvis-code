"""Voice engine selection with graceful fallback to Noop."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from jarvis_code.config import VoiceConfig, VoiceEngineName
from jarvis_code.utils.logging import get_logger

from .engine import VoiceEngine
from .noop import NoopVoiceEngine
from .piper import PiperVoiceEngine
from .system import SystemVoiceEngine

_log = get_logger("voice")


def _have(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


def engine_available(name: VoiceEngineName, voice: VoiceConfig) -> bool:
    """Sync availability check used for selection (mirrors engine.is_available)."""
    if name == VoiceEngineName.NOOP:
        return True
    if name == VoiceEngineName.SYSTEM:
        return _have("pyttsx3")
    if name == VoiceEngineName.PIPER:
        return (
            _have("piper")
            and _have("sounddevice")
            and bool(voice.model_path)
            and Path(voice.model_path).expanduser().is_file()
        )
    return False


def _construct(name: VoiceEngineName, voice: VoiceConfig) -> VoiceEngine:
    if name == VoiceEngineName.PIPER:
        return PiperVoiceEngine(voice.model_path, voice.config_path)
    if name == VoiceEngineName.SYSTEM:
        return SystemVoiceEngine()
    return NoopVoiceEngine()


def select_engine(voice: VoiceConfig) -> VoiceEngine:
    """Return the first available engine in [configured, fallback, noop]."""
    order: list[VoiceEngineName] = []
    for candidate in (voice.engine, voice.fallback_engine, VoiceEngineName.NOOP):
        if candidate not in order:
            order.append(candidate)
    for name in order:
        if engine_available(name, voice):
            if name != voice.engine:
                _log.info("voice: '%s' unavailable, using '%s'", voice.engine.value, name.value)
            return _construct(name, voice)
    return NoopVoiceEngine()  # pragma: no cover - noop is always available above
