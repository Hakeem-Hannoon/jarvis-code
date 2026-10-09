"""Read-only git tools (git_diff, git_status). Run with cwd = sandbox root.

Even "read-only" git can be tricked into running arbitrary programs that a
hostile repository configures: external/textconv diff drivers (run by
``git diff``) and ``core.fsmonitor`` (run by ``git status``). We neutralise those
with ``git diff --no-ext-diff --no-textconv`` (which override any repo/global
config *and* the GIT_* env vars), a ``-c core.fsmonitor=`` override, never launch
a pager, ignore system config, and run with the secret-scrubbed environment. So
these tools stay genuinely read-only and cannot execute repo-controlled code —
important because GIT_READ is allowed in every mode, including ``plan``.
"""

from __future__ import annotations

import shutil
import subprocess

from jarvis_code.permissions.modes import Capability
from jarvis_code.utils.logging import scrub_secret_env

from .base import Tool, ToolContext, ToolResult

# ``-c`` overrides take precedence over repo-local/global config, so a hostile
# ``.git/config`` cannot re-enable the fsmonitor hook program that `status` runs.
_GIT_HARDENING = [
    "--no-pager",
    "-c", "core.fsmonitor=",   # no fsmonitor program (runs during `status`)
]


def _git_env() -> dict[str, str]:
    env = scrub_secret_env()
    env["GIT_CONFIG_NOSYSTEM"] = "1"   # ignore /etc/gitconfig
    env["GIT_TERMINAL_PROMPT"] = "0"   # never block on a credential prompt
    # These env vars are *another* way to set an external diff/pager program and
    # are not covered by `-c`, so drop them outright.
    for var in ("GIT_EXTERNAL_DIFF", "GIT_PAGER"):
        env.pop(var, None)
    return env


def _run_git(ctx: ToolContext, git_args: list[str]) -> ToolResult:
    if shutil.which("git") is None:
        return ToolResult.fail("git is not installed or not on PATH.")
    workdir = ctx.sandbox.validate_cwd(None)  # always the sandbox root
    try:
        proc = subprocess.run(
            ["git", *_GIT_HARDENING, *git_args],
            cwd=str(workdir),
            env=_git_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",  # a diff of a non-UTF-8 file must not raise
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
        # --no-ext-diff/--no-textconv: never run a repo-configured external-diff
        # or textconv filter program (these override config AND the GIT_* env).
        git_args = ["diff", "--no-ext-diff", "--no-textconv"]
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
