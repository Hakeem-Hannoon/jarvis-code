"""VoiceEngine protocol + options.

Engines speak only short, curated status strings (see ``voice.messages``). They
never receive raw model output, file contents, shell output, or secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class SpeakOptions:
    voice: str = "jarvis-inspired"  # style preset name only — not a cloned voice
    rate: float = 1.0
    volume: float = 0.9
    pitch: float = 1.0  # 1.0 = natural; lower = deeper/synthetic (macOS `say`)
    robotic: float = 0.0  # 0 = natural intonation; 1 = flat/monotone delivery


@runtime_checkable
class VoiceEngine(Protocol):
    name: str

    async def speak(self, text: str, options: SpeakOptions | None = None) -> None: ...
    async def stop(self) -> None: ...
    async def is_available(self) -> bool: ...
