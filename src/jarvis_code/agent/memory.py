"""Project memory: read at session start, written back at session end.

Memory lives in ``JARVIS.md`` (or ``.jarvis/memory.md``) inside the sandbox. On
start it is loaded into the system prompt as guidance that ranks **below**
explicit user instructions and the safety rules. At the end of a session jarvis
writes a concise, auto-maintained notes block back to ``JARVIS.md`` so the next
session has context — without clobbering any human-written content (the auto
block lives between markers; everything else is preserved).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jarvis_code.sandbox import Sandbox, SandboxViolation
from jarvis_code.utils.logging import redact

from .state import RunState

# Search order; the first that exists wins.
MEMORY_CANDIDATES = ("JARVIS.md", ".jarvis/memory.md")
_MEMORY_FILE = "JARVIS.md"

_MAX_MEMORY_CHARS = 8_000

# The auto-maintained region. Anything outside these markers is the user's and
# is preserved verbatim across saves.
_AUTO_BEGIN = "<!-- jarvis:auto-notes — maintained automatically; edit outside the markers -->"
_AUTO_END = "<!-- /jarvis:auto-notes -->"


def load_project_memory(sandbox: Sandbox) -> str | None:
    """Return project-memory text if present (truncated), else None."""
    for candidate in MEMORY_CANDIDATES:
        try:
            if sandbox.exists(candidate):
                text = sandbox.read(candidate)
            else:
                continue
        except (SandboxViolation, OSError, UnicodeDecodeError):
            continue
        text = text.strip()
        if not text:
            continue
        if len(text) > _MAX_MEMORY_CHARS:
            text = text[:_MAX_MEMORY_CHARS] + "\n... [memory truncated]"
        return text
    return None


def has_notes_to_save(state: RunState) -> bool:
    """True when a session did enough to be worth remembering."""
    return bool(
        state.goal
        or state.files_changed
        or state.commands_run
        or state.key_decisions
        or state.subagent_summaries
    )


def _auto_block(state: RunState, *, when: str) -> str:
    """The delimited, secret-redacted auto-notes block (markers included)."""
    lines = [
        "## jarvis notes",
        f"_Updated {when}. Auto-maintained — context for the next session._",
    ]
    if state.goal:
        lines.append("\n### Last task\n" + state.goal.strip())
    if state.files_changed:
        recent = state.files_changed[-25:]
        lines.append("\n### Files recently changed\n" + "\n".join(f"- `{f}`" for f in recent))
    if state.commands_run:
        lines.append(f"\n### Commands run last session\n{len(state.commands_run)} command(s).")
    if state.key_decisions:
        lines.append("\n### Decisions\n" + "\n".join(f"- {d}" for d in state.key_decisions[-15:]))
    if state.pending_tasks:
        lines.append("\n### Open items\n" + "\n".join(f"- {t}" for t in state.pending_tasks[-15:]))
    if state.subagent_summaries:
        subs = state.subagent_summaries[-8:]
        lines.append("\n### Sub-agent results\n" + "\n".join(f"- {s}" for s in subs))
    body = redact("\n".join(lines).strip())
    return f"{_AUTO_BEGIN}\n{body}\n{_AUTO_END}\n"


def save_project_memory(
    sandbox: Sandbox, state: RunState, *, when: str | None = None
) -> Path | None:
    """Write/refresh the auto-notes block in ``JARVIS.md``; preserve user content.

    No-op (returns None) when the session has nothing worth remembering or the
    write fails — saving notes must never crash a session.
    """
    if not has_notes_to_save(state):
        return None
    when = when or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    block = _auto_block(state, when=when)

    try:
        existing = sandbox.read(_MEMORY_FILE) if sandbox.exists(_MEMORY_FILE) else ""
    except (SandboxViolation, OSError, UnicodeDecodeError):
        existing = ""

    if _AUTO_BEGIN in existing and _AUTO_END in existing:
        head, _, rest = existing.partition(_AUTO_BEGIN)
        _, _, tail = rest.partition(_AUTO_END)
        new = head.rstrip()
        new = (new + "\n\n" if new else "") + block + tail.lstrip("\n")
    elif existing.strip():
        new = block + "\n" + existing.lstrip("\n")
    else:
        new = block

    try:
        return sandbox.write(_MEMORY_FILE, new)
    except (SandboxViolation, OSError):
        return None
