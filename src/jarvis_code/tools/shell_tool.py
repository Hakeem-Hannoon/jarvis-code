"""run_shell: subprocess execution with a sandbox-confined working directory.

Enforcement model (important):
- The *enforced* guarantee here is the working directory: it is the sandbox root
  or a validated subdirectory, never outside (see ``Sandbox.validate_cwd``).
- A small set of catastrophic / sandbox-escaping patterns is screened out in
  *every* mode (even ``dangerous``) as defense in depth, and the attempt is
  logged. This is best-effort string screening — per the invariants we do NOT
  rely on string matching for real containment.
- Full process isolation (preventing a shell from reading outside via absolute
  paths) needs OS-level sandboxing (containers/seccomp); that is a documented
  deferral. Until then, ``auto`` mode asks before every shell command, showing
  command/cwd/why/effect/risk, and ``plan`` forbids shell entirely.
"""

from __future__ import annotations

import re
import subprocess

from jarvis_code.permissions.manager import RiskLevel
from jarvis_code.permissions.modes import Capability
from jarvis_code.sandbox import SandboxViolation
from jarvis_code.utils.logging import get_logger

from .base import Tool, ToolContext, ToolResult

_log = get_logger("tools.shell")


class ShellCommandBlocked(Exception):
    """A command matched a catastrophic pattern and was refused outright."""


# Patterns refused in all modes. Kept deliberately small and high-confidence.
_BLOCKED = [
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|/\*|~|\$HOME)(\s|$)"), "recursive delete of / or home"),
    (re.compile(r"\brm\s+-[a-z]*f?r[a-z]*\s+(/|/\*|~|\$HOME)(\s|$)"), "recursive delete of / or home"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "fork bomb"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "filesystem format"),
    (re.compile(r"\bdd\b[^\n]*\bof=/dev/"), "raw write to a device"),
    (re.compile(r">\s*/dev/sd[a-z]"), "raw write to a disk device"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"), "host power control"),
]

# Heuristics that merely *raise the displayed risk* (never auto-block).
_HIGH_RISK = [
    re.compile(r"\bsudo\b"),
    re.compile(r"\brm\b\s+-[a-z]*r"),
    re.compile(r"\bcurl\b[^\n]*\|\s*(sh|bash)\b"),
    re.compile(r"\bwget\b[^\n]*\|\s*(sh|bash)\b"),
    re.compile(r">\s*/etc/"),
    re.compile(r"\bgit\s+push\b"),
]
_MEDIUM_RISK = [
    re.compile(r"\b(rm|mv|cp)\b"),
    re.compile(r"\b(pip|npm|pnpm|yarn|brew|apt|apt-get)\b\s+(install|add|remove|uninstall)"),
    re.compile(r"\bgit\s+(commit|reset|checkout|clean|rebase|merge)\b"),
    re.compile(r">"),
]


def screen_shell_command(command: str) -> None:
    """Raise :class:`ShellCommandBlocked` for catastrophic patterns; else pass."""
    for pattern, reason in _BLOCKED:
        if pattern.search(command):
            _log.warning("blocked shell command (%s): %r", reason, command)
            raise ShellCommandBlocked(f"Refused: {reason}.")


def assess_shell_risk(command: str) -> RiskLevel:
    """Best-effort risk level for display in the approval prompt."""
    for pattern in _HIGH_RISK:
        if pattern.search(command):
            return RiskLevel.HIGH
    for pattern in _MEDIUM_RISK:
        if pattern.search(command):
            return RiskLevel.MEDIUM
    return RiskLevel.LOW


class RunShellTool(Tool):
    name = "run_shell"
    description = (
        "Run a shell command with the working directory confined to the sandbox. "
        "Set cwd to a subdirectory relative to the root if needed."
    )
    capability = Capability.SHELL
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string", "description": "Subdirectory relative to root (optional)."},
        },
        "required": ["command"],
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        command = str(self._require(args, "command"))
        cwd_arg = args.get("cwd")

        try:
            screen_shell_command(command)
        except ShellCommandBlocked as exc:
            return ToolResult.fail(str(exc), blocked=True)

        # The real enforcement: cwd must resolve inside the sandbox root.
        try:
            workdir = ctx.sandbox.validate_cwd(cwd_arg)
        except SandboxViolation as exc:
            return ToolResult.fail(str(exc))

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=ctx.shell_timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.fail(f"command timed out after {ctx.shell_timeout}s")
        except OSError as exc:  # pragma: no cover - exotic exec failures
            return ToolResult.fail(str(exc))

        combined = proc.stdout
        if proc.stderr:
            combined += ("\n" if combined else "") + proc.stderr
        output = self._truncate(combined, ctx)
        return ToolResult(
            ok=proc.returncode == 0,
            output=output,
            error=None if proc.returncode == 0 else f"exit code {proc.returncode}",
            metadata={
                "command": command,
                "cwd": ctx.sandbox.relpath(workdir),
                "returncode": proc.returncode,
            },
        )
