"""Classify a live user instruction into how it should be handled.

Heuristic and deterministic. Order matters: hard interrupt > soft interrupt >
permission response > question > steering (the default).
"""

from __future__ import annotations

import re

from .types import InstructionKind

_HARD = re.compile(r"\b(stop( now| everything)?|abort|halt|kill it|cancel everything)\b", re.I)
_SOFT = re.compile(r"\b(wait|hold on|hang on|pause|one sec|hold up|actually)\b", re.I)
_PERMISSION_YES = re.compile(r"^\s*(1|y|yes|yeah|yep|approve|approved|go ahead|do it|sure|ok|okay)\s*$", re.I)
_PERMISSION_NO = re.compile(r"^\s*(2|n|no|nope|deny|denied|don'?t|stop that)\s*$", re.I)
_QUESTION_WORDS = re.compile(r"^\s*(what|why|how|where|when|which|who|whose|can you|could you|are you|is it|do you|did you|does)\b", re.I)


def classify(text: str) -> InstructionKind:
    t = text.strip()
    if not t:
        return InstructionKind.STEERING
    if _HARD.search(t):
        return InstructionKind.HARD_INTERRUPT
    if _SOFT.search(t):
        return InstructionKind.SOFT_INTERRUPT
    if _PERMISSION_YES.match(t) or _PERMISSION_NO.match(t) or t in ("3",):
        return InstructionKind.PERMISSION_RESPONSE
    if t.endswith("?") or _QUESTION_WORDS.match(t):
        return InstructionKind.QUESTION
    return InstructionKind.STEERING
