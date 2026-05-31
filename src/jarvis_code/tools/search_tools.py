"""Search tools: filename globbing and text grep, confined to the sandbox."""

from __future__ import annotations

import fnmatch
import re

from jarvis_code.permissions.modes import Capability
from jarvis_code.sandbox import SandboxViolation

from .base import Tool, ToolContext, ToolResult

# Directories we never descend into when searching (noise / large binaries).
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".jarvis"}

_MAX_MATCHES = 1000


def _pruned_files(ctx: ToolContext, base: str):
    for f in ctx.sandbox.iter_files(base):
        parts = set(f.parts)
        if parts & _SKIP_DIRS:
            continue
        yield f


class SearchFilesTool(Tool):
    name = "search_files"
    description = "Find files by glob pattern (e.g. '*.py') within the sandbox."
    capability = Capability.SEARCH
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob like '*.py' or 'test_*'."},
            "path": {"type": "string", "description": "Directory to search (default '.')."},
        },
        "required": ["pattern"],
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        pattern = str(self._require(args, "pattern"))
        base = args.get("path", ".")
        try:
            matches = []
            for f in _pruned_files(ctx, base):
                if fnmatch.fnmatch(f.name, pattern):
                    matches.append(ctx.sandbox.relpath(f))
                    if len(matches) >= _MAX_MATCHES:
                        break
        except (SandboxViolation, OSError) as exc:
            return ToolResult.fail(str(exc))
        matches.sort()
        return ToolResult.success("\n".join(matches), count=len(matches))


class GrepTextTool(Tool):
    name = "grep_text"
    description = "Search file contents for a regular expression within the sandbox."
    capability = Capability.SEARCH
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regular expression."},
            "path": {"type": "string", "description": "Directory to search (default '.')."},
            "glob": {"type": "string", "description": "Optional filename glob filter."},
            "ignore_case": {"type": "boolean", "default": False},
        },
        "required": ["pattern"],
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        pattern = str(self._require(args, "pattern"))
        base = args.get("path", ".")
        glob = args.get("glob")
        flags = re.IGNORECASE if args.get("ignore_case") else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return ToolResult.fail(f"invalid regex: {exc}")

        results: list[str] = []
        try:
            for f in _pruned_files(ctx, base):
                if glob and not fnmatch.fnmatch(f.name, glob):
                    continue
                try:
                    text = f.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue  # skip binary/unreadable files
                rel = ctx.sandbox.relpath(f)
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        results.append(f"{rel}:{lineno}: {line.strip()}")
                        if len(results) >= _MAX_MATCHES:
                            break
                if len(results) >= _MAX_MATCHES:
                    break
        except (SandboxViolation, OSError) as exc:
            return ToolResult.fail(str(exc))
        return ToolResult.success(self._truncate("\n".join(results), ctx), count=len(results))
