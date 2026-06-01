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
# Cinematic, calm-assistant tone (think a certain Iron Man AI). Still short,
# fixed, curated status strings — never raw model output, and still redacted.
_CATALOG: dict[str, str] = {
    "scanning": "Surveying the project.",
    "reading": "Accessing the file.",
    "searching": "Sweeping the codebase.",
    "editing": "Applying modifications.",
    "writing": "Writing to disk.",
    "running_tests": "Running diagnostics.",
    "running_command": "Executing your command.",
    "found_failing_test": "I've isolated the failing test, sir.",
    "launching_subagent": "Deploying a specialist unit.",
    "requesting_permission": "Awaiting your authorization.",
    "permission_denied": "Understood. Rerouting.",
    "done": "All systems nominal. Task complete, sir.",
    "blocked": "I require further direction, sir.",
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
    return shorten(f"Awaiting your authorization to run {program}, sir.")


# File tools whose spoken status names the file (basename only).
_FILE_ACTION_VERB: dict[str, str] = {
    "read_file": "Accessing",
    "write_file": "Writing",
    "edit_file": "Modifying",
    "delete_file": "Removing",
    "move_file": "Relocating",
    "create_directory": "Creating",
}
FILE_TOOLS = frozenset(_FILE_ACTION_VERB)


def _basename(summary: str | None) -> str:
    """The final path component only (never the full path / directory tree)."""
    s = (summary or "").strip().replace("\\", "/").rstrip("/")
    if not s:
        return ""
    return s.rsplit("/", 1)[-1][:60]


def specific_action_phrase(tool_name: str, summary: str | None) -> str:
    """A curated action phrase, made specific where it is SAFE.

    File tools name the file by **basename only** (e.g. "Writing config.toml.")
    — never the full path. Shell names the program (first token only). All other
    arguments are dropped, and the result is still secret-redacted by ``shorten``.
    """
    if tool_name == "run_shell":
        tokens = (summary or "").strip().split()
        if tokens:
            return shorten(f"Executing the {tokens[0]} command.")
        return _CATALOG["running_command"]
    verb = _FILE_ACTION_VERB.get(tool_name)
    if verb:
        name = _basename(summary)
        if name:
            return shorten(f"{verb} {name}.")
        return tool_status_phrase(tool_name) or f"{verb} a file."
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
    """The line to speak for a tool.

    File operations always name the file ("Writing config.toml.") — that is more
    useful than a generic reason. For other tools, an opted-in ``intent`` (the
    model's short reason) is spoken when present, else the specific phrase.
    """
    if tool_name in FILE_TOOLS:
        return specific_action_phrase(tool_name, summary)
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
