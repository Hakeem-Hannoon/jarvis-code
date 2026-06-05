"""Conversation context with simple, deterministic compaction.

Holds the system prompt plus the running message list. When the estimated size
exceeds the budget, the oldest non-recent messages are collapsed into a single
summary message built from the run's preserved state — keeping goal, files
changed, commands, errors, permissions, and sub-agent results intact.
"""

from __future__ import annotations

from jarvis_code.models import Message, ModelResponse, Role, ToolCall
from jarvis_code.utils.logging import redact

from .state import RunState


class ConversationContext:
    """The model's view of the conversation.

    Every string that can reach the model is routed through :func:`redact`
    *here*, at the single boundary where context is assembled, so a registered
    secret (a provider API key) can never enter model context — even via tool
    output (file reads, shell output, sub-agent summaries), which is untrusted.
    Any new method that adds model-visible text MUST redact it too.
    """

    def __init__(
        self,
        system_prompt: str,
        *,
        max_chars: int = 60_000,
        keep_recent: int = 6,
    ) -> None:
        # Provider keys are registered with the redactor at config load, which
        # always precedes runtime/context assembly, so redacting once here is safe.
        self.system_prompt = redact(system_prompt)
        self.messages: list[Message] = []
        self.max_chars = max_chars
        self.keep_recent = keep_recent

    # -- mutation ----------------------------------------------------------

    def add_user(self, text: str) -> None:
        self.messages.append(Message.user(redact(text)))

    def add_assistant(self, response: ModelResponse) -> None:
        self.messages.append(
            Message.assistant(content=redact(response.text), tool_calls=response.tool_calls)
        )

    def add_tool_result(self, tool_call_id: str, name: str, output: str) -> None:
        # Tool output is untrusted and is the most likely way a secret could
        # reach the model (e.g. reading the config file or `env`). Redact it.
        self.messages.append(Message.tool_result(tool_call_id, name, redact(output)))

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

    def compact(self, state: RunState, *, force: bool = False) -> bool:
        """Collapse old messages into a preservation summary. Returns True if done.

        Auto-compaction passes ``force=False`` (only when over budget); the
        ``/compress`` command passes ``force=True`` to compact on demand. Either
        way there must be more than ``keep_recent`` messages to collapse.
        """
        if not force and not self.needs_compaction():
            return False
        if len(self.messages) <= self.keep_recent:
            return False

        recent = self.messages[-self.keep_recent :]
        # If the kept window would start on a TOOL result, include one more so a
        # provider never sees an orphan tool_result without its tool_use.
        summary = Message.user(
            redact(
                "[context compacted to save space; preserved facts follow]\n"
                + state.preservation_summary()
            )
        )
        self.messages = [summary, *recent]
        return True
