"""Filesystem tools. Every path goes through the sandbox first."""

from __future__ import annotations

from jarvis_code.permissions.modes import Capability
from jarvis_code.sandbox import SandboxViolation
from jarvis_code.utils.diffs import change_summary, unified_diff

from .base import Tool, ToolContext, ToolResult

_PATH_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string", "description": "Path relative to the sandbox root."}},
    "required": ["path"],
}


class ListFilesTool(Tool):
    name = "list_files"
    description = "List the entries of a directory inside the sandbox."
    capability = Capability.READ
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path (default '.')."}
        },
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        path = args.get("path", ".")
        try:
            entries = ctx.sandbox.list(path)
        except SandboxViolation as exc:
            return ToolResult.fail(str(exc))
        names = []
        for entry in entries:
            suffix = "/" if entry.is_dir() else ""
            names.append(ctx.sandbox.relpath(entry) + suffix)
        return ToolResult.success("\n".join(names), count=len(names))


_DEFAULT_READ_LINES = 2000


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a UTF-8 text file inside the sandbox. Large files are paginated by "
        "line: pass 'offset' (1-based start line) and 'limit' to read further "
        "chunks. Prefer this over shell head/tail — it is sandboxed and needs no "
        "approval."
    )
    capability = Capability.READ
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the sandbox root."},
            "offset": {
                "type": "integer",
                "description": "1-based line number to start at (default 1).",
            },
            "limit": {
                "type": "integer",
                "description": f"Max lines to return (default {_DEFAULT_READ_LINES}).",
            },
        },
        "required": ["path"],
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        path = self._require(args, "path")
        try:
            content = ctx.sandbox.read(path)
        except (SandboxViolation, OSError, UnicodeDecodeError) as exc:
            return ToolResult.fail(str(exc))

        lines = content.splitlines()
        total = len(lines)
        offset = self._as_int(args.get("offset"), default=1, minimum=1)
        limit = self._as_int(args.get("limit"), default=_DEFAULT_READ_LINES, minimum=1)
        start = offset - 1
        chunk = lines[start : start + limit]
        body = "\n".join(chunk)
        end = start + len(chunk)

        # A continuation hint keeps the model paginating via this tool instead of
        # falling back to shell head/tail (which needs approval and isn't sandboxed).
        note = ""
        if end < total:
            note = (
                f"\n\n... [showing lines {offset}–{end} of {total}. "
                f"Call read_file with offset={end + 1} to continue.]"
            )
        elif offset > 1:
            note = f"\n\n... [showing lines {offset}–{end} of {total} (end of file).]"

        rel = ctx.sandbox.relpath(path)
        return ToolResult.success(
            self._truncate(body, ctx) + note, path=rel, lines=total, shown=(offset, end)
        )

    @staticmethod
    def _as_int(value: object, *, default: int, minimum: int) -> int:
        try:
            n = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
        return max(minimum, n)


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a text file inside the sandbox."
    capability = Capability.WRITE
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        path = self._require(args, "path")
        content = self._require(args, "content")
        try:
            old = ctx.sandbox.read(path) if ctx.sandbox.exists(path) else ""
            resolved = ctx.sandbox.write(path, str(content))
        except (SandboxViolation, OSError) as exc:
            return ToolResult.fail(str(exc))
        rel = ctx.sandbox.relpath(resolved)
        return ToolResult.success(
            f"Wrote {rel} ({change_summary(old, str(content))}).",
            path=rel,
            files_changed=[rel],
        )


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Replace an exact substring in a file. Set replace_all to change every "
        "occurrence; otherwise old_string must be unique."
    )
    capability = Capability.EDIT
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        path = self._require(args, "path")
        old_string = str(self._require(args, "old_string"))
        new_string = str(self._require(args, "new_string"))
        replace_all = bool(args.get("replace_all", False))
        try:
            content = ctx.sandbox.read(path)
        except (SandboxViolation, OSError, UnicodeDecodeError) as exc:
            return ToolResult.fail(str(exc))

        count = content.count(old_string)
        if count == 0:
            return ToolResult.fail("old_string not found in file.")
        if count > 1 and not replace_all:
            return ToolResult.fail(
                f"old_string is not unique ({count} matches); pass replace_all=true "
                "or provide more context."
            )
        updated = content.replace(old_string, new_string)
        try:
            resolved = ctx.sandbox.write(path, updated)
        except (SandboxViolation, OSError) as exc:
            return ToolResult.fail(str(exc))
        rel = ctx.sandbox.relpath(resolved)
        return ToolResult.success(
            f"Edited {rel} ({change_summary(content, updated)}).",
            path=rel,
            files_changed=[rel],
            diff=unified_diff(content, updated, rel),
        )


class DeleteFileTool(Tool):
    name = "delete_file"
    description = "Delete a file or directory inside the sandbox."
    capability = Capability.DELETE
    parameters = _PATH_SCHEMA

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        path = self._require(args, "path")
        try:
            rel = ctx.sandbox.relpath(path)
            ctx.sandbox.delete(path)
        except (SandboxViolation, OSError) as exc:
            return ToolResult.fail(str(exc))
        return ToolResult.success(f"Deleted {rel}.", path=rel, files_changed=[rel])


class CreateDirectoryTool(Tool):
    name = "create_directory"
    description = "Create a directory (and parents) inside the sandbox."
    capability = Capability.WRITE
    parameters = _PATH_SCHEMA

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        path = self._require(args, "path")
        try:
            resolved = ctx.sandbox.mkdir(path)
        except (SandboxViolation, OSError) as exc:
            return ToolResult.fail(str(exc))
        rel = ctx.sandbox.relpath(resolved)
        return ToolResult.success(f"Created directory {rel}.", path=rel)


class MoveFileTool(Tool):
    name = "move_file"
    description = "Move or rename a file/directory within the sandbox."
    capability = Capability.MOVE
    parameters = {
        "type": "object",
        "properties": {
            "src": {"type": "string"},
            "dst": {"type": "string"},
        },
        "required": ["src", "dst"],
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        src = self._require(args, "src")
        dst = self._require(args, "dst")
        try:
            src_rel = ctx.sandbox.relpath(src)
            resolved = ctx.sandbox.move(src, dst)
        except (SandboxViolation, OSError) as exc:
            return ToolResult.fail(str(exc))
        dst_rel = ctx.sandbox.relpath(resolved)
        return ToolResult.success(
            f"Moved {src_rel} -> {dst_rel}.",
            files_changed=[src_rel, dst_rel],
        )
