"""Read-only git tools (git_diff, git_status). Run with cwd = sandbox root."""

from __future__ import annotations

import shutil
import subprocess

from jarvis_code.permissions.modes import Capability

from .base import Tool, ToolContext, ToolResult


def _run_git(ctx: ToolContext, git_args: list[str]) -> ToolResult:
    if shutil.which("git") is None:
        return ToolResult.fail("git is not installed or not on PATH.")
    workdir = ctx.sandbox.validate_cwd(None)  # always the sandbox root
    try:
        proc = subprocess.run(
            ["git", *git_args],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=ctx.shell_timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult.fail("git command timed out")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()
        if "not a git repository" in err.lower():
            return ToolResult.fail("not a git repository")
        return ToolResult.fail(err or f"git exited {proc.returncode}")
    out = proc.stdout
    if len(out) > ctx.max_output_chars:
        out = out[: ctx.max_output_chars] + "\n... [truncated]"
    return ToolResult.success(out)


class GitStatusTool(Tool):
    name = "git_status"
    description = "Show the git working-tree status of the sandbox repository."
    capability = Capability.GIT_READ
    parameters = {"type": "object", "properties": {}}

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        return _run_git(ctx, ["status", "--short", "--branch"])


class GitDiffTool(Tool):
    name = "git_diff"
    description = "Show the git diff of the sandbox repository (optionally a path)."
    capability = Capability.GIT_READ
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Limit the diff to a path (optional)."},
            "staged": {"type": "boolean", "default": False},
        },
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        git_args = ["diff"]
        if args.get("staged"):
            git_args.append("--staged")
        path = args.get("path")
        if path:
            # Validate the path is in-root before handing it to git.
            try:
                ctx.sandbox.safe_path(path)
            except Exception as exc:  # SandboxViolation
                return ToolResult.fail(str(exc))
            git_args += ["--", str(path)]
        return _run_git(ctx, git_args)
