"""Tool registry + the full deterministic tool set, all sandbox-routed."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolResult
from .executor import ExecutionResult, ToolExecutor
from .fs_tools import (
    CreateDirectoryTool,
    DeleteFileTool,
    EditFileTool,
    ListFilesTool,
    MoveFileTool,
    ReadFileTool,
    WriteFileTool,
)
from .git_tools import GitDiffTool, GitStatusTool
from .registry import ToolRegistry
from .search_tools import GrepTextTool, SearchFilesTool
from .shell_tool import RunShellTool, ShellCommandBlocked, assess_shell_risk, screen_shell_command
from .subagent_tool import SpawnSubagentTool

_DEFAULT_TOOLS = [
    ListFilesTool,
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    DeleteFileTool,
    SearchFilesTool,
    GrepTextTool,
    RunShellTool,
    GitDiffTool,
    GitStatusTool,
    CreateDirectoryTool,
    MoveFileTool,
    SpawnSubagentTool,
]


def build_default_registry(*, include_subagents: bool = True) -> ToolRegistry:
    """Construct a registry with the documented tool set.

    ``include_subagents=False`` omits spawn_subagent so the model can't delegate
    (sub-agents are unlocked only at ``madmax`` effort).
    """
    registry = ToolRegistry()
    for tool_cls in _DEFAULT_TOOLS:
        if tool_cls is SpawnSubagentTool and not include_subagents:
            continue
        registry.register(tool_cls())
    return registry


__all__ = [
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolRegistry",
    "ToolExecutor",
    "ExecutionResult",
    "build_default_registry",
    "ShellCommandBlocked",
    "assess_shell_risk",
    "screen_shell_command",
]
