"""A prompter that lets queued live voice/text *decline or redirect* a prompt.

When a permission prompt comes up, an instruction already waiting in the queue
may answer it — but only in the safe direction: "no" declines, and anything
else becomes "Do Something Else" with the text as steering feedback.

A queued "yes" is **never** treated as approval. It was typed (or spoken) before
this request existed, so the user has not seen the command or diff they would be
approving — `ok` typed as a passing acknowledgement must not pre-authorize the
next shell command. Approval always comes from the inner prompter, which shows
the request first. This is one permission path — not a parallel system.
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


def response_from_queue(queue: InstructionQueue) -> tuple[PermissionResponse | None, int]:
    """Answer a prompt from the queue if that is safe to do.

    Returns ``(response, stale_approvals_dropped)``. ``response`` is ``None`` when
    nothing queued can answer (ask the user for real). Queued approvals are
    discarded — see the module docstring.
    """
    dropped = 0
    while True:
        instr = queue.pop_next()
        if instr is None:
            return None, dropped
        response = map_text_to_response(instr.text)
        if response.choice == Choice.YES:
            dropped += 1  # stale: predates the request it would be approving
            continue
        return response, dropped


class QueueAwarePrompter:
    """Routes a queued live instruction to the active prompt; else asks inner."""

    def __init__(self, queue: InstructionQueue, inner: Prompter | None = None) -> None:
        self._queue = queue
        self._inner = inner

    def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        response, _dropped = response_from_queue(self._queue)
        if response is not None:
            return response
        if self._inner is not None:
            return self._inner.request_permission(request)
        # Fail closed: no usable queued response and no interactive prompter.
        return PermissionResponse(choice=Choice.NO)
