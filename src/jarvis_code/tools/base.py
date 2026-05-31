"""Tool base types.

Tools are deterministic: they execute already-authorized actions and route every
filesystem access through the sandbox. Tools never decide permissions — the
permission manager does that before a tool runs. Each tool declares the
:class:`Capability` it needs so the manager can gate it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from jarvis_code.permissions.modes import Capability
from jarvis_code.sandbox import Sandbox


@dataclass
class ToolContext:
    """Everything a tool may need to run, supplied by the runtime."""

    sandbox: Sandbox
    # Set in Phase 10; spawn_subagent uses it. None means "not available yet".
    orchestrator: object | None = None
    # Hard cap on captured output to keep context/transcripts bounded.
    max_output_chars: int = 20_000
    shell_timeout: float = 120.0


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def fail(cls, error: str, **metadata: object) -> "ToolResult":
        return cls(ok=False, output="", error=error, metadata=dict(metadata))

    @classmethod
    def success(cls, output: str = "", **metadata: object) -> "ToolResult":
        return cls(ok=True, output=output, error=None, metadata=dict(metadata))


class Tool(ABC):
    """Base class for all tools."""

    #: Unique tool name exposed to the model.
    name: str
    #: One-line description for the model's tool list.
    description: str
    #: Permission class used to gate the tool.
    capability: Capability
    #: JSON schema for the tool's arguments (used by the provider layer).
    parameters: dict

    @abstractmethod
    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        """Execute the tool with validated, already-authorized arguments."""
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------

    def _require(self, args: dict, key: str) -> object:
        if key not in args or args[key] is None:
            raise ValueError(f"{self.name}: missing required argument '{key}'")
        return args[key]

    def _truncate(self, text: str, ctx: ToolContext) -> str:
        if len(text) <= ctx.max_output_chars:
            return text
        head = text[: ctx.max_output_chars]
        return head + f"\n... [truncated {len(text) - ctx.max_output_chars} chars]"
