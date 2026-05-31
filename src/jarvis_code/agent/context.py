"""Conversation context with simple, deterministic compaction.

Holds the system prompt plus the running message list. When the estimated size
exceeds the budget, the oldest non-recent messages are collapsed into a single
summary message built from the run's preserved state — keeping goal, files
changed, commands, errors, permissions, and sub-agent results intact.
"""

from __future__ import annotations

from jarvis_code.models import Message, ModelResponse, Role, ToolCall

from .state import RunState


class ConversationContext:
    def __init__(
        self,
        system_prompt: str,
        *,
        max_chars: int = 60_000,
        keep_recent: int = 6,
    ) -> None:
        self.system_prompt = system_prompt
        self.messages: list[Message] = []
        self.max_chars = max_chars
        self.keep_recent = keep_recent

    # -- mutation ----------------------------------------------------------

    def add_user(self, text: str) -> None:
        self.messages.append(Message.user(text))

    def add_assistant(self, response: ModelResponse) -> None:
        self.messages.append(
            Message.assistant(content=response.text, tool_calls=response.tool_calls)
        )

    def add_tool_result(self, tool_call_id: str, name: str, output: str) -> None:
        self.messages.append(Message.tool_result(tool_call_id, name, output))

    # -- building ----------------------------------------------------------

    def build(self) -> list[Message]:
        return [Message.system(self.system_prompt), *self.messages]

    def estimated_chars(self) -> int:
        total = len(self.system_prompt)
        for m in self.messages:
            total += len(m.content)
            for tc in m.tool_calls:
                total += len(tc.name) + len(str(tc.arguments))
        return total

    # -- compaction --------------------------------------------------------

    def needs_compaction(self) -> bool:
        return self.estimated_chars() > self.max_chars

    def compact(self, state: RunState) -> bool:
        """Collapse old messages into a preservation summary. Returns True if done."""
        if not self.needs_compaction():
            return False
        if len(self.messages) <= self.keep_recent:
            return False

        recent = self.messages[-self.keep_recent :]
        # If the kept window would start on a TOOL result, include one more so a
        # provider never sees an orphan tool_result without its tool_use.
        summary = Message.user(
            "[context compacted to save space; preserved facts follow]\n"
            + state.preservation_summary()
        )
        self.messages = [summary, *recent]
        return True
