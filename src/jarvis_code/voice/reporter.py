"""Bridge the agent's Reporter hooks to curated voice announcements.

Critically, ``assistant_text`` does NOT speak raw model output — only curated
status phrases derived from tool names and permission outcomes are ever voiced.
"""

from __future__ import annotations

from .queue import VoiceSubsystem


class VoiceReporter:
    def __init__(self, subsystem: VoiceSubsystem) -> None:
        self._vs = subsystem

    def assistant_text(self, text: str) -> None:
        # Deliberately silent: raw model text is never spoken.
        return None

    def tool_started(self, name: str, summary: str) -> None:
        if name == "run_shell" and self._vs.voice.announce_permissions:
            # The permission request is announced by `permission` below; here we
            # only voice non-shell tool activity to avoid double-speaking.
            return
        self._vs.announce_tool(name)

    def tool_finished(self, name: str, ok: bool, detail: str) -> None:
        return None

    def permission(self, tool: str, outcome: str, reason: str | None) -> None:
        if outcome == "denied":
            self._vs.announce_status("permission_denied")

    def info(self, text: str) -> None:
        return None
