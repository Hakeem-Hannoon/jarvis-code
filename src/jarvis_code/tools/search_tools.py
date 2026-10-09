"""Search tools: filename globbing and text grep, confined to the sandbox."""

from __future__ import annotations

import fnmatch
import re

from jarvis_code.permissions.modes import Capability
from jarvis_code.sandbox import SandboxViolation

from .base import Tool, ToolContext, ToolResult
from .protected import is_protected_file

# Directories we never descend into when searching (noise / large binaries).
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".jarvis"}

_MAX_MATCHES = 1000
# Don't slurp huge files (minified bundles, data dumps) into memory to grep them.
_MAX_GREP_BYTES = 2_000_000


def _pruned_files(ctx: ToolContext, base: str):
    """In-root files under ``base``, skipping noise dirs *below* it.

    jarvis's own config files are never yielded: they can hold API keys.
    """
    for f in ctx.sandbox.walk_files(base, skip_dirs=_SKIP_DIRS):
        if not is_protected_file(ctx.sandbox, f):
            yield f


def _rel(ctx: ToolContext, f) -> str:
    """Root-relative display path of a walked file (the entry, not a link target)."""
    try:
        return f.relative_to(ctx.sandbox.root).as_posix()
    except ValueError:  # pragma: no cover - walk only yields in-root paths
        return ctx.sandbox.relpath(f)


def _glob_matches(pattern: str, name: str, rel: str) -> bool:
    """Match a glob against the file name, or the relative path if it has a '/'."""
    if "/" not in pattern:
        return fnmatch.fnmatch(name, pattern)
    if fnmatch.fnmatch(rel, pattern):
        return True
    # "**/x" should also match x at the top level.
    return pattern.startswith("**/") and _glob_matches(pattern[3:], name, rel)


class SearchFilesTool(Tool):
    name = "search_files"
    description = (
        "Find files by glob pattern within the sandbox. A bare pattern like '*.py' "
        "matches file names anywhere; a pattern with '/' (e.g. 'src/**/*.py') "
        "matches the path relative to the sandbox root."
    )
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
                rel = _rel(ctx, f)
                if _glob_matches(pattern, f.name, rel):
                    matches.append(rel)
                    if len(matches) >= _MAX_MATCHES:
                        break
        except (SandboxViolation, OSError) as exc:
            return ToolResult.fail(str(exc))
        matches.sort()
        output = "\n".join(matches)
        if len(matches) >= _MAX_MATCHES:
            output += f"\n... [stopped at {_MAX_MATCHES} matches; narrow the pattern or path]"
        return ToolResult.success(output, count=len(matches))


class GrepTextTool(Tool):
    name = "grep_text"
    description = "Search file contents for a regular expression within the sandbox."
    capability = Capability.SEARCH
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regular expression."},
            "path": {
                "type": "string",
                "description": "Directory or single file to search (default '.').",
            },
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
                rel = _rel(ctx, f)
                if glob and not _glob_matches(str(glob), f.name, rel):
                    continue
                try:
                    if f.stat().st_size > _MAX_GREP_BYTES:
                        continue  # skip huge files rather than read them whole
                    text = f.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue  # skip binary/unreadable files
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        results.append(f"{rel}:{lineno}: {line.strip()}")
                        if len(results) >= _MAX_MATCHES:
                            break
                if len(results) >= _MAX_MATCHES:
                    break
        except (SandboxViolation, OSError) as exc:
            return ToolResult.fail(str(exc))
        output = self._truncate("\n".join(results), ctx)
        if len(results) >= _MAX_MATCHES:
            output += f"\n... [stopped at {_MAX_MATCHES} matches; narrow the pattern or path]"
        return ToolResult.success(output, count=len(results))
