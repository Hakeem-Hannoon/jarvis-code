"""Project memory loading.

Memory lives in ``JARVIS.md`` or ``.jarvis/memory.md`` inside the sandbox. It is
treated as guidance that ranks **below** explicit user instructions and the
safety rules (the system prompt makes that precedence explicit).
"""

from __future__ import annotations

from jarvis_code.sandbox import Sandbox, SandboxViolation

# Search order; the first that exists wins.
MEMORY_CANDIDATES = ("JARVIS.md", ".jarvis/memory.md")

_MAX_MEMORY_CHARS = 8_000


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
