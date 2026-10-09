"""Agent runtime loop, context management, compaction, and project memory."""

from __future__ import annotations

from .context import ConversationContext
from .controls import ProviderInfo, SessionControls, SessionSettings, split_model_spec
from .loop import AgentRuntime, RunResult, RunStatus, assemble_runtime
from .memory import (
    MEMORY_CANDIDATES,
    has_notes_to_save,
    load_project_memory,
    save_project_memory,
)
from .prompts import build_system_prompt
from .state import RunState

__all__ = [
    "ConversationContext",
    "ProviderInfo",
    "SessionControls",
    "SessionSettings",
    "split_model_spec",
    "AgentRuntime",
    "RunResult",
    "RunStatus",
    "assemble_runtime",
    "MEMORY_CANDIDATES",
    "load_project_memory",
    "save_project_memory",
    "has_notes_to_save",
    "build_system_prompt",
    "RunState",
]
