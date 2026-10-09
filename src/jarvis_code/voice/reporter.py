"""Bridge the agent's Reporter hooks to curated voice announcements.

By default ``assistant_text`` does NOT speak raw model output — only curated
status phrases derived from tool names and permission outcomes are voiced.

When ``voice.speak_intent`` is explicitly opted in, the model's short lead-in
reason (cleaned, capped, secret-redacted via :mod:`.messages`) may be spoken.

**One line per model turn.** A model step usually fires several tool calls in a
row; narrating each one ("Accessing x.", "Modifying y.", "Writing z.") is too
chatty. So the reporter speaks at most once per turn — the *reason* — and stays
quiet for the remaining tools of that turn. A new ``assistant_text`` (the loop
emits one per model message, loop.py) starts the next turn and refreshes the
budget. Identical back-to-back lines are also suppressed.
"""

from __future__ import annotations

from . import messages
from .queue import VoiceSubsystem


class VoiceReporter:
    def __init__(self, subsystem: VoiceSubsystem) -> None:
        self._vs = subsystem
        self._speak_intent = subsystem.voice.speak_intent
        self._pending_intent: str | None = None
        # Once-per-turn speech budget (reset by ``assistant_text``) so a batch of
        # tool calls doesn't produce a line of narration each.
        self._spoke_this_turn = False
        self._last_phrase: str | None = None  # suppress identical consecutive lines

    def _say(self, phrase: str | None) -> None:
        if phrase and phrase != self._last_phrase:
            self._vs.say(phrase)
            self._last_phrase = phrase

    def assistant_text(self, text: str) -> None:
        # A new model message = a new turn: refresh the once-per-turn budget.
        # Never spoken directly; when opted in, retained as the reason to voice.
        self._spoke_this_turn = False
        if self._speak_intent:
            self._pending_intent = text
        return None

    def tool_started(self, name: str, summary: str) -> None:
        if self._spoke_this_turn:
            return  # already spoke once this turn — skip the per-tool chatter
        if self._speak_intent:
            # Speak the model's short reason; if this turn carried none, fall back
            # to ONE generic curated phrase (never the per-file name).
            intent = self._pending_intent
            self._pending_intent = None
            phrase = messages.clean_intent(intent) if intent else None
            if phrase is None:
                phrase = messages.tool_status_phrase(name)
            self._spoke_this_turn = True
            self._say(phrase)
            return
        if name == "run_shell" and self._vs.voice.announce_permissions:
            # A shell command that needs approval is announced by
            # `awaiting_approval`; staying quiet here avoids double-speaking.
            return
        self._spoke_this_turn = True
        self._say(messages.tool_status_phrase(name))

    def action(self, request: object) -> None:
        # The boxed command / diff is a visual (console) concern; nothing spoken.
        return None

    def tool_finished(self, name: str, ok: bool, detail: str) -> None:
        return None

    def awaiting_approval(self, request: object) -> None:
        # The one line worth interrupting for: the agent is blocked on the user.
        # Only the program name is ever voiced (never arguments).
        self._vs.announce_permission(getattr(request, "command", None))

    def permission(self, tool: str, outcome: str, reason: str | None) -> None:
        if outcome == "denied":
            self._vs.announce_status("permission_denied")

    def info(self, text: str) -> None:
        return None

    def thinking(self, active: bool) -> None:
        return None
