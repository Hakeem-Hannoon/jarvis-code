"""Bridge the agent's Reporter hooks to curated voice announcements.

By default ``assistant_text`` does NOT speak raw model output — only curated
status phrases derived from tool names and permission outcomes are voiced.

When ``voice.speak_intent`` is explicitly opted in, the model's short lead-in
reason (cleaned, capped, secret-redacted via :mod:`.messages`) may be spoken
instead of the fixed phrase. It is voiced at most once per model turn.
"""

from __future__ import annotations

from . import messages
from .queue import VoiceSubsystem


class VoiceReporter:
    def __init__(self, subsystem: VoiceSubsystem) -> None:
        self._vs = subsystem
        self._speak_intent = subsystem.voice.speak_intent
        self._pending_intent: str | None = None

    def assistant_text(self, text: str) -> None:
        # Never spoken directly. When opted in, retain it as the one-line reason
        # to attach to the next tool announcement.
        if self._speak_intent:
            self._pending_intent = text
        return None

    def tool_started(self, name: str, summary: str) -> None:
        if self._speak_intent:
            # Speak the model's reason once, then fall back to curated phrases
            # for any further tools in the same turn.
            intent = self._pending_intent
            self._pending_intent = None
            phrase = messages.action_phrase(name, summary, intent=intent)
            if phrase:
                self._vs.say(phrase)
            return
        if name == "run_shell" and self._vs.voice.announce_permissions:
            # The permission request is announced by `permission` below; here we
            # only voice non-shell tool activity to avoid double-speaking.
            return
        self._vs.announce_tool(name)

    def action(self, request: object) -> None:
        # The boxed command / diff is a visual (console) concern; nothing spoken.
        return None

    def tool_finished(self, name: str, ok: bool, detail: str) -> None:
        return None

    def permission(self, tool: str, outcome: str, reason: str | None) -> None:
        if outcome == "denied":
            self._vs.announce_status("permission_denied")

    def info(self, text: str) -> None:
        return None
