"""Tests for the interactive chat loop (bare `jarvis`)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis_code.agent.loop import RunResult, RunStatus
from jarvis_code.agent.state import RunState
from jarvis_code.cli.chat import chat_loop
from jarvis_code.models import ProviderError
from jarvis_code.permissions import PermissionMode


class FakeRuntime:
    def __init__(self, error_on=None) -> None:
        self.runs: list[str] = []
        self.error_on = error_on
        self.context = SimpleNamespace(added=[], add_user=lambda t: self.context.added.append(t))
        self.executor = SimpleNamespace(
            permissions=SimpleNamespace(mode=PermissionMode.AUTO),
            ctx=SimpleNamespace(sandbox=SimpleNamespace(root="/tmp/proj")),
        )

    async def run(self, message: str) -> RunResult:
        if self.error_on == message:
            raise ProviderError("provider boom")
        self.runs.append(message)
        return RunResult(RunStatus.COMPLETE, f"echo {message}", 1, RunState())


def _prompt_from(lines):
    it = iter(lines)

    def prompt_fn(_prompt: str) -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return prompt_fn


@pytest.mark.asyncio
async def test_chat_runs_turns_skips_commands_and_exits() -> None:
    out: list[str] = []
    rt = FakeRuntime()
    await chat_loop(
        rt,
        prompt_fn=_prompt_from(["hello agent", "", "second message", "/exit"]),
        echo=out.append,
    )
    # Blank lines and slash commands are not sent as turns.
    assert rt.runs == ["hello agent", "second message"]
    assert any("Bye" in o for o in out)


@pytest.mark.asyncio
async def test_chat_exits_on_eof() -> None:
    out: list[str] = []
    rt = FakeRuntime()
    await chat_loop(rt, prompt_fn=_prompt_from([]), echo=out.append)  # immediate EOF
    assert rt.runs == []
    assert any("Bye" in o for o in out)


@pytest.mark.asyncio
async def test_chat_mode_switch() -> None:
    out: list[str] = []
    rt = FakeRuntime()
    await chat_loop(
        rt,
        prompt_fn=_prompt_from(["/mode plan", "/mode bogus", "/exit"]),
        echo=out.append,
    )
    assert rt.executor.permissions.mode == PermissionMode.PLAN
    assert any("usage: /mode" in o for o in out)
    assert "[mode changed to plan]" in rt.context.added


@pytest.mark.asyncio
async def test_chat_help_and_sandbox() -> None:
    out: list[str] = []
    rt = FakeRuntime()
    await chat_loop(rt, prompt_fn=_prompt_from(["/help", "/sandbox", "/exit"]), echo=out.append)
    assert any("Commands:" in o for o in out)
    assert any("/tmp/proj" in o for o in out)
    assert rt.runs == []


@pytest.mark.asyncio
async def test_chat_handles_provider_error_and_continues() -> None:
    out: list[str] = []
    rt = FakeRuntime(error_on="will fail")
    await chat_loop(
        rt,
        prompt_fn=_prompt_from(["will fail", "still here", "/exit"]),
        echo=out.append,
    )
    assert rt.runs == ["still here"]  # survived the error, kept going
    assert any("provider boom" in o for o in out)
