"""Readline line editing for the chat prompt.

Enables arrow keys (←/→ move, ↑/↓ history), persistent cross-session history, and
the usual Emacs shortcuts (Ctrl-A/E, Ctrl-K/U, Ctrl-W, …). Everything degrades
silently if ``readline`` is unavailable — the prompt still works, just without
editing niceties.
"""

from __future__ import annotations

from pathlib import Path

from jarvis_code.config import global_config_path

_HISTORY_LEN = 1000
_active = False  # True once readline is wired up (used to size colored prompts)


def history_path() -> Path:
    """Per-user chat history file (alongside the global config)."""
    return global_config_path().parent / "history"


def setup_line_editing() -> bool:
    """Enable readline editing and load persistent history. No-op if absent."""
    global _active
    try:
        import readline
    except Exception:  # pragma: no cover - readline missing on some builds
        _active = False
        return False
    try:
        readline.set_history_length(_HISTORY_LEN)
        path = history_path()
        if path.exists():
            readline.read_history_file(str(path))
    except Exception:  # pragma: no cover - corrupt/locked history is non-fatal
        pass
    _active = True
    return True


def save_history() -> None:
    """Persist the chat history (owner-only — prompts may be sensitive)."""
    if not _active:
        return
    try:
        import readline

        path = history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        readline.write_history_file(str(path))
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover - non-POSIX
            pass
    except Exception:  # pragma: no cover - best effort
        pass


def colored_prompt(visible: str, ansi: str = "1;36") -> str:
    """A colored prompt string.

    When readline is active the ANSI escapes are wrapped in the non-printing
    markers (\\001…\\002) readline needs to measure width correctly — without
    them, editing a long line mis-positions the cursor. When readline is not
    active, fall back to plain ``typer`` styling.
    """
    if _active:
        return f"\001\033[{ansi}m\002{visible}\001\033[0m\002"
    import typer

    return typer.style(visible, fg=typer.colors.CYAN, bold=True)
