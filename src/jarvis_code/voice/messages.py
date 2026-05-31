"""Curated voice message generation.

The ONLY text the voice subsystem ever speaks comes from here: a small catalog
of short, fixed status phrases (with very limited, sanitized interpolation).
Raw model output, file contents, shell output, and secrets are never spoken.
``shorten`` is a final safety net: it caps length and runs text through the
secret redactor.
"""

from __future__ import annotations

from jarvis_code.utils.logging import redact

# Curated, fixed phrases. Keyed by an internal status event.
_CATALOG: dict[str, str] = {
    "scanning": "Scanning the project.",
    "reading": "Reading a file.",
    "searching": "Searching the project.",
    "editing": "Applying an edit.",
    "writing": "Writing a file.",
    "running_tests": "Running the test suite.",
    "running_command": "Running a command.",
    "found_failing_test": "I found the failing test.",
    "launching_subagent": "Launching a focused sub-agent.",
    "requesting_permission": "Requesting permission to run a command.",
    "permission_denied": "Permission denied. Choosing another route.",
    "done": "Task complete.",
    "blocked": "I am blocked and need direction.",
}

# Map tool names to a curated status key (so we never voice tool args/output).
_TOOL_STATUS: dict[str, str] = {
    "list_files": "scanning",
    "read_file": "reading",
    "search_files": "searching",
    "grep_text": "searching",
    "edit_file": "editing",
    "write_file": "writing",
    "create_directory": "writing",
    "delete_file": "editing",
    "move_file": "editing",
    "run_shell": "running_command",
    "git_status": "scanning",
    "git_diff": "scanning",
    "spawn_subagent": "launching_subagent",
}


def status_phrase(key: str) -> str | None:
    """Return the curated phrase for a status key, or None if unknown."""
    return _CATALOG.get(key)


def tool_status_phrase(tool_name: str) -> str | None:
    """Curated phrase for a tool, derived from the tool NAME only."""
    key = _TOOL_STATUS.get(tool_name)
    return _CATALOG.get(key) if key else None


def permission_request_phrase(command: str | None) -> str:
    """Spoken permission request.

    To avoid voicing command arguments (which could contain a secret), only the
    program name (first token) is spoken; the rest is dropped. Still redacted as
    a final safety net.
    """
    program = "a command"
    if command:
        tokens = command.strip().split()
        if tokens:
            program = tokens[0]
    return shorten(f"I need permission to run {program} inside the sandbox.")


def swarm_phrase(phrase: str) -> str:
    return shorten(phrase or "Launching the swarm.")


def shorten(text: str, max_chars: int = 160) -> str:
    """Cap length at a word boundary and scrub any registered secret."""
    text = redact(text).strip()
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars].rsplit(" ", 1)[0]
    return clipped + "…"
