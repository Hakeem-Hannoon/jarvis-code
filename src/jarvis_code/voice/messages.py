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


def specific_action_phrase(tool_name: str, summary: str | None) -> str:
    """A curated action phrase, made a bit more specific where it is SAFE.

    For shell, the program name (first token only) is included — the same safe
    subset spoken in :func:`permission_request_phrase`. Arguments are never
    voiced. For everything else this is the plain curated phrase.
    """
    if tool_name == "run_shell":
        tokens = (summary or "").strip().split()
        if tokens:
            return shorten(f"Running the {tokens[0]} command.")
        return _CATALOG["running_command"]
    return tool_status_phrase(tool_name) or "Working."


def clean_intent(text: str | None) -> str | None:
    """Reduce model lead-in text to one short spoken line (capped + redacted).

    Used ONLY when ``voice.speak_intent`` is opted in. Returns None when nothing
    speakable remains. This is the one place model-authored words may be voiced;
    ``shorten`` still caps length and scrubs registered secrets as a safety net.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None
    # First sentence / first line only — keep it short and status-like.
    first = stripped.splitlines()[0]
    for sep in (". ", "! ", "? "):
        if sep in first:
            first = first.split(sep, 1)[0]
            break
    first = first.strip().rstrip(".!?").strip()
    if not first:
        return None
    spoken = shorten(first, max_chars=140)
    return spoken or None


def action_phrase(tool_name: str, summary: str | None, intent: str | None = None) -> str:
    """The line to speak for a tool. With an opted-in ``intent``, speak the
    model's short reason; otherwise speak the curated/specific action phrase."""
    cleaned = clean_intent(intent)
    if cleaned:
        return cleaned
    return specific_action_phrase(tool_name, summary)


def swarm_phrase(phrase: str) -> str:
    return shorten(phrase or "Launching the swarm.")


def shorten(text: str, max_chars: int = 160) -> str:
    """Cap length at a word boundary and scrub any registered secret."""
    text = redact(text).strip()
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars].rsplit(" ", 1)[0]
    return clipped + "…"
