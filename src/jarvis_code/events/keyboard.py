"""Keyboard controls mapping (documented in the README).

The real raw-tty capture loop is a thin platform-specific wrapper around
``handle_key`` so the mapping itself is deterministic and testable.

Controls:
    M       cycle mic mode (Open Mic -> Push to Talk -> Muted)
    V       toggle push-to-talk recording
    Space   hold-to-talk (press/release) in Push to Talk mode
    Esc     soft interrupt (finish the current safe step, then adjust)
    Ctrl+C  hard stop / exit
    ?       show controls
"""

from __future__ import annotations

from enum import Enum


class KeyAction(str, Enum):
    CYCLE_MIC = "cycle_mic"
    TOGGLE_PTT = "toggle_ptt"
    HOLD_TO_TALK = "hold_to_talk"
    SOFT_INTERRUPT = "soft_interrupt"
    HARD_STOP = "hard_stop"
    SHOW_CONTROLS = "show_controls"
    NONE = "none"


# Normalized key name -> action.
KEY_BINDINGS: dict[str, KeyAction] = {
    "m": KeyAction.CYCLE_MIC,
    "v": KeyAction.TOGGLE_PTT,
    "space": KeyAction.HOLD_TO_TALK,
    "esc": KeyAction.SOFT_INTERRUPT,
    "ctrl+c": KeyAction.HARD_STOP,
    "?": KeyAction.SHOW_CONTROLS,
}


def action_for_key(key: str) -> KeyAction:
    return KEY_BINDINGS.get(key.strip().lower(), KeyAction.NONE)


def describe_controls() -> str:
    return (
        "Controls:\n"
        "  M       cycle mic mode (Open Mic / Push to Talk / Muted)\n"
        "  V       toggle push-to-talk recording\n"
        "  Space   hold-to-talk in Push to Talk mode\n"
        "  Esc     soft interrupt (adjust after the current step)\n"
        "  Ctrl+C  hard stop / exit\n"
        "  ?       show controls"
    )
