"""Classify a live user instruction into how it should be handled.

Heuristic and deterministic. Order matters: hard interrupt > soft interrupt >
permission response > question > steering (the default).
"""

from __future__ import annotations

import re

from .types import InstructionKind

# A hard interrupt is a *command to stop*, not any sentence containing the word:
# "don't stop the dev server" or "add an abort button" is steering. So the stop
# word must lead the message and be followed only by filler ("stop now",
# "stop doing that") or punctuation ("stop, that's wrong").
_STOP_WORD = r"(?:stop|abort|halt|cancel|kill it)"
_STOP_FILLER = (
    r"(?:now|it|that|this|everything|all|please|right|immediately|jarvis|doing|working|running)"
)
_HARD = re.compile(
    rf"^\s*(?:please\s+|jarvis[,\s]+)?{_STOP_WORD}(?:\s+{_STOP_FILLER})*\s*(?:[.!,;:\-—].*)?$",
    re.I | re.S,
)
_SOFT = re.compile(r"\b(wait|hold on|hang on|pause|one sec|hold up|actually)\b", re.I)
_PERMISSION_YES = re.compile(r"^\s*(1|y|yes|yeah|yep|approve|approved|go ahead|do it|sure|ok|okay)\s*$", re.I)
_PERMISSION_NO = re.compile(r"^\s*(2|n|no|nope|deny|denied|don'?t|stop that)\s*$", re.I)
_QUESTION_WORDS = re.compile(r"^\s*(what|why|how|where|when|which|who|whose|can you|could you|are you|is it|do you|did you|does)\b", re.I)


def classify(text: str) -> InstructionKind:
    t = text.strip()
    if not t:
        return InstructionKind.STEERING
    if _HARD.match(t):
        return InstructionKind.HARD_INTERRUPT
    if _SOFT.search(t):
        return InstructionKind.SOFT_INTERRUPT
    if _PERMISSION_YES.match(t) or _PERMISSION_NO.match(t) or t in ("3",):
        return InstructionKind.PERMISSION_RESPONSE
    if t.endswith("?") or _QUESTION_WORDS.match(t):
        return InstructionKind.QUESTION
    return InstructionKind.STEERING
