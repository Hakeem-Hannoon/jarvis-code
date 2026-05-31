"""A prompter that lets live voice/text answer the SAME 1/2/3 permission menu.

When a permission prompt is active, the next queued instruction is interpreted
first as a possible permission response: "yes"/"no" map to choices 1/2, and
anything else becomes "Do Something Else" (3) with the spoken text as steering
feedback. If nothing is queued, it defers to an inner prompter (the terminal
menu). This is one permission path — not a parallel system.
"""

from __future__ import annotations

from jarvis_code.permissions import (
    Choice,
    PermissionRequest,
    PermissionResponse,
    Prompter,
)

from .classify import _PERMISSION_NO, _PERMISSION_YES
from .queue import InstructionQueue


def map_text_to_response(text: str) -> PermissionResponse:
    t = text.strip()
    if _PERMISSION_YES.match(t) or t == "1":
        return PermissionResponse(choice=Choice.YES)
    if _PERMISSION_NO.match(t) or t == "2":
        return PermissionResponse(choice=Choice.NO)
    # Anything else (including "3"/"do something else") collects the text as
    # steering feedback for the model to revise with.
    feedback = None if t in ("3", "do something else", "else") else t
    return PermissionResponse(choice=Choice.SOMETHING_ELSE, feedback=feedback)


class QueueAwarePrompter:
    """Routes a queued live instruction to the active prompt; else asks inner."""

    def __init__(self, queue: InstructionQueue, inner: Prompter | None = None) -> None:
        self._queue = queue
        self._inner = inner

    def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        instr = self._queue.pop_next()
        if instr is not None:
            return map_text_to_response(instr.text)
        if self._inner is not None:
            return self._inner.request_permission(request)
        # Fail closed: no queued response and no interactive prompter.
        return PermissionResponse(choice=Choice.NO)
