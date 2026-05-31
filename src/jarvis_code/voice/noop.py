"""The silent fallback engine: always available, never crashes, says nothing."""

from __future__ import annotations

from .engine import SpeakOptions


class NoopVoiceEngine:
    name = "noop"

    def __init__(self) -> None:
        self.spoken: list[str] = []  # recorded for visibility/tests; not played

    async def speak(self, text: str, options: SpeakOptions | None = None) -> None:
        self.spoken.append(text)

    async def stop(self) -> None:
        return None

    async def is_available(self) -> bool:
        return True
