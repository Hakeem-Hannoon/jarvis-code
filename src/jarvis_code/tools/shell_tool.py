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

import os
import re
import shlex
import signal
import subprocess

from jarvis_code.permissions.manager import RiskLevel
from jarvis_code.permissions.modes import Capability
from jarvis_code.sandbox import SandboxViolation
from jarvis_code.utils.logging import get_logger, scrub_secret_env

from .base import Tool, ToolContext, ToolResult

_log = get_logger("tools.shell")


class ShellCommandBlocked(Exception):
    """A command matched a catastrophic pattern and was refused outright."""


# -- screening ---------------------------------------------------------------
#
# Screening looks at what would actually be *executed*: the command is split
# into simple commands and each program name + its arguments is checked. That
# keeps `grep -r "shutdown" .` and `git commit -m "fix reboot loop"` working
# (a word-anywhere regex refused both) while still catching spellings such as
# `rm -Rf /`, `rm -r -f /`, `sudo rm -rf --no-preserve-root /` and
# `sh -c 'rm -rf ~'`. Anything the tokenizer can't parse falls back to the
# conservative regexes below.

_FORK_BOMB = re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")

# Fallback patterns for text shlex cannot tokenize (e.g. an unbalanced quote).
_FALLBACK_BLOCKED = [
    (re.compile(r"\brm\s+(-\S+\s+)*-\S*[rR]\S*\s+(-\S+\s+)*[\"']?(/|/\*|~|~/|\$HOME|\$\{HOME\})[\"']?(\s|$)"),
     "recursive delete of / or home"),
    (re.compile(r"\bmkfs(\.\w+)?\s"), "filesystem format"),
    (re.compile(r"\bdd\b[^\n]*\bof=/dev/(?!null\b|stdout\b|stderr\b)"), "raw write to a device"),
    (re.compile(r">\s*/dev/(sd|hd|vd|nvme|disk|mmcblk)"), "raw write to a disk device"),
    (re.compile(r"(^|[;&|]\s*)(sudo\s+)?(shutdown|reboot|halt|poweroff)\b"), "host power control"),
]

_POWER = {"shutdown", "reboot", "halt", "poweroff"}
_CATASTROPHIC_TARGETS = {
    "/", "/*", "/.", "~", "~/", "~/*", "$HOME", "$HOME/", "$HOME/*",
    "${HOME}", "${HOME}/", "${HOME}/*",
}
_DISK_DEVICE = re.compile(r"^/dev/(sd|hd|vd|nvme|disk|mmcblk)")
_HARMLESS_DEVICES = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/zero")
# Words that precede the real program in a simple command.
_PREFIX_WORDS = {"{", "!", "if", "then", "else", "elif", "do", "while", "until", "time"}
_WRAPPERS = {"sudo", "doas", "nohup", "env", "command", "exec", "nice", "ionice",
             "stdbuf", "timeout", "xargs", "builtin"}
# Wrapper options that consume the following token (so it isn't the program).
_WRAPPER_VALUE_OPTS = {"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "-n", "-s", "-k"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
_SEPARATORS = set(";&|()")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _simple_commands(line: str) -> list[list[str]]:
    """Split one line into simple commands (lists of words). May raise ValueError."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    commands: list[list[str]] = [[]]
    for token in lexer:
        if token and set(token) <= _SEPARATORS:
            commands.append([])
        else:
            commands[-1].append(token)
    return [c for c in commands if c]


def _program_and_args(words: list[str]) -> tuple[str, list[str]]:
    """Strip env assignments / wrappers (sudo, env, timeout…) to the real program."""
    i = 0
    while i < len(words):
        word = words[i]
        if word in _PREFIX_WORDS or _ASSIGNMENT.match(word):
            i += 1
            continue
        if os.path.basename(word) in _WRAPPERS:
            wrapper = os.path.basename(word)
            i += 1
            while i < len(words) and (words[i].startswith("-") or _ASSIGNMENT.match(words[i])):
                i += 2 if words[i] in _WRAPPER_VALUE_OPTS else 1
            if wrapper == "timeout" and i < len(words):
                i += 1  # the duration
            continue
        return os.path.basename(word), words[i + 1 :]
    return "", []


def _screen_simple(words: list[str], depth: int) -> str | None:
    """Return the reason a simple command is catastrophic, or None."""
    # Redirections live among the words: `> /dev/sda`.
    for prev, word in zip(words, words[1:]):
        if set(prev) <= {">", "<"} and ">" in prev and _DISK_DEVICE.match(word):
            return "raw write to a disk device"

    program, args = _program_and_args(words)
    if not program:
        return None
    if program in _POWER:
        return "host power control"
    if program == "systemctl" and _POWER & set(args):
        return "host power control"
    if program == "init" and args[:1] in (["0"], ["6"]):
        return "host power control"
    if program == "mkfs" or program.startswith("mkfs."):
        return "filesystem format"
    if program == "dd":
        for arg in args:
            if arg.startswith("of=/dev/") and not arg[3:].startswith(_HARMLESS_DEVICES):
                return "raw write to a device"
    if program == "rm":
        flags = [a for a in args if a.startswith("-")]
        recursive = any(
            a == "--recursive" or (not a.startswith("--") and ("r" in a or "R" in a))
            for a in flags
        )
        if recursive and any(a in _CATASTROPHIC_TARGETS for a in args):
            return "recursive delete of / or home"
    if program in _SHELLS | {"eval"} and depth < 3:
        # `sh -c '<script>'` / `eval '<script>'`: screen the nested script too.
        if program == "eval":
            nested = [" ".join(args)]
        elif "-c" in args:
            nested = args[args.index("-c") + 1 :][:1]
        else:
            nested = []
        for script in nested:
            reason = _screen_text(script, depth + 1)
            if reason:
                return reason
    return None


def _screen_text(command: str, depth: int = 0) -> str | None:
    if _FORK_BOMB.search(command):
        return "fork bomb"
    for line in command.splitlines():
        try:
            simple = _simple_commands(line)
        except ValueError:
            # Unparseable (e.g. a quote spanning lines): be conservative.
            for pattern, reason in _FALLBACK_BLOCKED:
                if pattern.search(line):
                    return reason
            continue
        for words in simple:
            reason = _screen_simple(words, depth)
            if reason:
                return reason
    return None


# Heuristics that merely *raise the displayed risk* (never auto-block).
_HIGH_RISK = [
    re.compile(r"\bsudo\b"),
    re.compile(r"\brm\b\s+-[a-zA-Z]*[rR]"),
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
    reason = _screen_text(command)
    if reason:
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
            returncode, stdout, stderr, timed_out = _run_confined(
                command, str(workdir), ctx.shell_timeout
            )
        except OSError as exc:  # pragma: no cover - exotic exec failures
            return ToolResult.fail(str(exc))

        combined = stdout
        if stderr:
            combined += ("\n" if combined else "") + stderr
        output = self._truncate(combined, ctx)
        metadata = {
            "command": command,
            "cwd": ctx.sandbox.relpath(workdir),
            "returncode": returncode,
        }
        if timed_out:
            # Keep whatever it printed before the deadline: that is usually the
            # clue to *why* it hung.
            return ToolResult(
                ok=False,
                output=output,
                error=f"command timed out after {ctx.shell_timeout:g}s (process tree killed)",
                metadata={**metadata, "timed_out": True},
            )
        return ToolResult(
            ok=returncode == 0,
            output=output,
            error=None if returncode == 0 else f"exit code {returncode}",
            metadata=metadata,
        )


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the command *and everything it spawned* (its whole process group)."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _run_confined(command: str, cwd: str, timeout: float) -> tuple[int, str, str, bool]:
    """Run ``command`` in ``cwd``; returns ``(returncode, stdout, stderr, timed_out)``.

    - stdin is ``/dev/null``: a command that reads input (a REPL, ``cat``, an
      editor git opens) gets EOF instead of silently stealing the user's
      keystrokes until the timeout.
    - The command runs in its own session/process group so that on timeout or
      Ctrl-C we can kill the whole tree — ``subprocess.run(timeout=...)`` only
      kills the shell and leaves its children (``npm run dev``, ``sleep``…)
      running forever.
    - Provider API keys (and other credential-shaped vars) are stripped from the
      child env so ``env`` can never read a key back into model context.
    """
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        env=scrub_secret_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout or "", stderr or "", False
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - pipe held open
            stdout, stderr = "", ""
        return proc.returncode if proc.returncode is not None else -9, stdout or "", stderr or "", True
    except BaseException:
        # Ctrl-C (or any other unwind): never leave the command running behind us.
        _kill_tree(proc)
        raise
