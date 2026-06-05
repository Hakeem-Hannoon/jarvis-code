"""Curated voice message generation.

The ONLY text the voice subsystem ever speaks comes from here: a small catalog
of short, fixed status phrases (with very limited, sanitized interpolation).
Raw model output, file contents, shell output, and secrets are never spoken.
``shorten`` is a final safety net: it caps length and runs text through the
secret redactor.
"""

from __future__ import annotations

import re

from jarvis_code.utils.logging import redact

# Symbols a TTS engine would otherwise read aloud BY NAME ("asterisk", "slash",
# "underscore", "backtick"…). Stripped from spoken text so only words are voiced.
# Ordinary sentence punctuation (. , ! ? ' : ; -) is kept for natural prosody.
_SPOKEN_SYMBOLS = re.compile(r"""[*_`~#|<>{}()\[\]$%^&=+@\\/"]+""")


def speakable(text: str) -> str:
    """Reduce a status string to spoken words.

    A path like ``src/app.py`` would otherwise be read "src slash app…" and
    ``**done**`` as "asterisk asterisk done"; the redaction marker ``***REDACTED***``
    as "asterisk asterisk asterisk…". This replaces those symbols with spaces so
    only words and sentence punctuation are spoken. Voice-only — console output
    and transcripts keep their symbols.
    """
    if not text:
        return text
    text = _SPOKEN_SYMBOLS.sub(" ", text).replace("…", " ")
    return re.sub(r"\s+", " ", text).strip()

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


def clean_intent(
    text: str | None, *, max_sentences: int = 2, max_chars: int = 200
) -> str | None:
    """Reduce model lead-in text to a short spoken line (capped + redacted).

    Used ONLY when ``voice.speak_intent`` is opted in. Keeps the model's opening
    one or two sentences — enough to say what it's doing and *why* — then caps
    length and scrubs registered secrets via ``shorten`` as a safety net. Returns
    None when nothing speakable remains. This is the one place model-authored
    words may be voiced; raw output, file contents, and secrets never are.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None
    # The lead-in paragraph (up to a blank line), collapsed to one spoken line.
    lead: list[str] = []
    for line in stripped.splitlines():
        if not line.strip():
            break
        lead.append(line.strip())
    blob = " ".join(lead).strip()
    if not blob:
        return None
    # Keep at most `max_sentences` sentences; drop only the trailing terminator
    # so a two-sentence reason still reads naturally when spoken.
    sentences = re.split(r"(?<=[.!?])\s+", blob)
    spoken = " ".join(sentences[:max_sentences]).strip().rstrip(".!?").strip()
    if not spoken:
        return None
    spoken = shorten(spoken, max_chars=max_chars)
    return spoken or None


def action_phrase(tool_name: str, summary: str | None, intent: str | None = None) -> str:
    """The line to speak for a tool.

    Prefer the model's short reason — what it's actually trying to build/figure
    out ("Refactoring the parser to handle nested calls") — over a bare file
    action. Fall back to the specific phrase (filename / program name) only when
    no reason is available.
    """
    cleaned = clean_intent(intent)
    if cleaned:
        return cleaned
    return specific_action_phrase(tool_name, summary)


def swarm_phrase(phrase: str) -> str:
    return shorten(phrase or "Launching the swarm.")


def shorten(text: str, max_chars: int = 160) -> str:
    """Scrub secrets, reduce to spoken words, and cap length at a word boundary.

    The single chokepoint for everything the voice engine speaks: redact →
    speakable (no symbol names) → length cap.
    """
    text = speakable(redact(text))
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0]
