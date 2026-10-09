"""Live steering: routing typed lines into the runtime while it works."""

from __future__ import annotations

import threading

import pytest

from jarvis_code.cli.steer import PausingQueuePrompter, SteerSession
from jarvis_code.events import InstructionKind, InstructionQueue
from jarvis_code.permissions import (
    Capability,
    Choice,
    DenyPrompter,
    PermissionRequest,
    PermissionResponse,
)


def _request() -> PermissionRequest:
    return PermissionRequest(
        tool_name="run_shell", capability=Capability.SHELL, summary="run a command"
    )


def _scripted(lines):
    it = iter(lines)

    def readline() -> str | None:
        return next(it, "")  # "" == EOF once exhausted

    return readline


# -- SteerSession routing ---------------------------------------------------


def test_steer_submits_and_classifies() -> None:
    q = InstructionQueue()
    s = SteerSession(q, readline=_scripted(["also add a test for this", "wait, use pytest"]))
    assert s._pump_once() is True   # steering
    assert s._pump_once() is True   # "wait, ..." -> soft interrupt
    assert s._pump_once() is False  # EOF -> stop
    kinds = [i.kind for i in q.drain()]
    assert kinds[0] is InstructionKind.STEERING
    assert kinds[1] is InstructionKind.SOFT_INTERRUPT


def test_steer_blank_lines_ignored() -> None:
    q = InstructionQueue()
    s = SteerSession(q, readline=_scripted(["   "]))
    assert s._pump_once() is True   # whitespace -> nothing queued
    assert len(q) == 0


def test_steer_announces_each_kind() -> None:
    out: list[str] = []
    q = InstructionQueue()
    s = SteerSession(q, echo=out.append, readline=_scripted(["stop now", "add docs too"]))
    s._pump_once()  # hard interrupt
    s._pump_once()  # steering
    assert any("stopping" in o for o in out)
    assert any("steering queued" in o for o in out)


def test_steer_stands_down_while_prompt_active() -> None:
    q = InstructionQueue()
    s = SteerSession(q, readline=_scripted(["this should NOT be read while prompting"]))
    s.prompt_active.set()
    assert s._pump_once() is True   # paused: returns without reading
    assert len(q) == 0              # nothing consumed from the reader


# -- PausingQueuePrompter ---------------------------------------------------


def test_prompter_never_approves_from_a_queued_yes() -> None:
    q = InstructionQueue()
    q.submit("yes")  # typed earlier — before any request existed
    notes: list[str] = []
    # The stale "yes" is dropped and the REAL prompt decides (here: deny).
    p = PausingQueuePrompter(q, DenyPrompter(), threading.Event(), echo=notes.append)
    assert p.request_permission(_request()).choice is Choice.NO
    assert any("ignored an earlier" in n for n in notes)


def test_steer_does_not_queue_a_bare_yes_or_no() -> None:
    out: list[str] = []
    q = InstructionQueue()
    s = SteerSession(q, echo=out.append, readline=_scripted(["ok", "no"]))
    s._pump_once()
    s._pump_once()
    assert len(q) == 0  # nothing queued to pre-answer a future approval prompt
    assert any("nothing is waiting" in o for o in out)


def test_prompter_queued_freeform_becomes_do_something_else() -> None:
    q = InstructionQueue()
    q.submit("use a temp dir instead")  # not yes/no -> steering feedback
    resp = PausingQueuePrompter(q, DenyPrompter(), threading.Event()).request_permission(_request())
    assert resp.choice is Choice.SOMETHING_ELSE
    assert resp.feedback == "use a temp dir instead"


def test_prompter_pauses_reader_then_asks_inner() -> None:
    q = InstructionQueue()  # empty -> must defer to inner
    flag = threading.Event()

    class _Inner:
        def request_permission(self, request) -> PermissionResponse:
            assert flag.is_set()  # the steering reader is paused during the ask
            return PermissionResponse(choice=Choice.NO)

    resp = PausingQueuePrompter(q, _Inner(), flag).request_permission(_request())
    assert resp.choice is Choice.NO
    assert not flag.is_set()  # flag cleared afterwards so the reader resumes


def test_steer_does_not_send_slash_commands_to_the_model() -> None:
    out: list[str] = []
    q = InstructionQueue()
    s = SteerSession(q, echo=out.append, readline=_scripted(["/status"]))
    s._pump_once()
    assert len(q) == 0  # "/status" is not an instruction for the agent
    assert any("works at the prompt" in o for o in out)
