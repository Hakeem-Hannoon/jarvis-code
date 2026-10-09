"""Provider-agnostic message/response types.

These are the *only* objects the agent loop exchanges with a provider. API keys
never appear here — they live on the provider instance and are used solely to
build HTTP headers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """A model's request to invoke a tool."""

    id: str
    name: str
    arguments: dict = field(default_factory=dict)
    # Opaque provider data that must be echoed back with the call (e.g. Gemini's
    # ``thoughtSignature``). Never shown to the user or put in a transcript.
    metadata: dict = field(default_factory=dict)


@dataclass
class Message:
    """One turn of conversation context (never contains secrets)."""

    role: Role
    content: str = ""
    # Set on ASSISTANT messages that requested tools.
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Set on TOOL messages (results fed back to the model).
    tool_call_id: str | None = None
    name: str | None = None

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role=Role.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role=Role.USER, content=content)

    @classmethod
    def assistant(cls, content: str = "", tool_calls: list[ToolCall] | None = None) -> "Message":
        return cls(role=Role.ASSISTANT, content=content, tool_calls=tool_calls or [])

    @classmethod
    def tool_result(cls, tool_call_id: str, name: str, content: str) -> "Message":
        return cls(role=Role.TOOL, content=content, tool_call_id=tool_call_id, name=name)


@dataclass
class ModelResponse:
    """A normalized provider response."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    usage: dict | None = None
    raw: dict | None = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    def token_usage(self) -> tuple[int, int]:
        """``(input_tokens, output_tokens)`` for this response, 0 when unknown.

        Each provider names these differently; normalize them here so the UI can
        show a running total without knowing which provider is in use.
        """
        usage = self.usage if isinstance(self.usage, dict) else {}

        def first(*keys: str) -> int:
            for key in keys:
                value = usage.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return int(value)
            return 0

        return (
            first("input_tokens", "prompt_tokens", "promptTokenCount"),
            first("output_tokens", "completion_tokens", "candidatesTokenCount"),
        )


# A tool spec is the registry's provider-agnostic dict: {name, description, parameters}.
ToolSpec = dict
