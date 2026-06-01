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


class FakeMic:
    def __init__(self, transcript=None, reason=None) -> None:
        self.enabled = True
        self._transcript = transcript
        self._reason = reason
        self.recorded = False

    async def availability_reason(self):
        return self._reason

    async def record_ptt(self, max_seconds: float = 30.0, *, stop=None):
        self.recorded = True
        if stop is not None:
            stop.set()
        return self._transcript

    def mic_bar(self) -> str:
        return "Mic: [>Push to Talk<]"


async def _immediate_stop() -> None:
    return None


@pytest.mark.asyncio
async def test_chat_talk_runs_transcribed_message() -> None:
    out: list[str] = []
    rt = FakeRuntime()
    mic = FakeMic(transcript="run the tests please")
    await chat_loop(
        rt,
        prompt_fn=_prompt_from(["/talk", "/exit"]),
        echo=out.append,
        mic=mic,
        stop_waiter=_immediate_stop,
    )
    assert mic.recorded is True
    assert rt.runs == ["run the tests please"]  # the transcript became a turn
    assert any("Recording" in o for o in out)


@pytest.mark.asyncio
async def test_chat_talk_reports_when_unavailable() -> None:
    out: list[str] = []
    rt = FakeRuntime()
    mic = FakeMic(reason="Voice input is off. In config set [voice.input] enabled = true")
    await chat_loop(
        rt,
        prompt_fn=_prompt_from(["/talk", "/exit"]),
        echo=out.append,
        mic=mic,
        stop_waiter=_immediate_stop,
    )
    assert mic.recorded is False
    assert rt.runs == []  # nothing was sent
    assert any("Voice input is off" in o for o in out)


@pytest.mark.asyncio
async def test_chat_talk_heard_nothing() -> None:
    out: list[str] = []
    rt = FakeRuntime()
    mic = FakeMic(transcript="   ")  # available, but empty transcript
    await chat_loop(
        rt,
        prompt_fn=_prompt_from(["/talk", "/exit"]),
        echo=out.append,
        mic=mic,
        stop_waiter=_immediate_stop,
    )
    assert rt.runs == []
    assert any("heard nothing" in o for o in out)


@pytest.mark.asyncio
async def test_chat_talk_without_mic_configured() -> None:
    out: list[str] = []
    rt = FakeRuntime()
    await chat_loop(
        rt,
        prompt_fn=_prompt_from(["/talk", "/exit"]),
        echo=out.append,
        mic=None,
        stop_waiter=_immediate_stop,
    )
    assert rt.runs == []
    assert any("isn't configured" in o for o in out)


# -- line editing + banner helpers ------------------------------------------


def test_history_path_lives_with_global_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    from jarvis_code.cli.lineedit import history_path

    p = history_path()
    assert p.name == "history" and "jarvis" in str(p)


def test_colored_prompt_readline_safe_only_when_active(monkeypatch) -> None:
    import jarvis_code.cli.lineedit as le

    monkeypatch.setattr(le, "_active", False)
    plain = le.colored_prompt("you ❯ ")
    assert "you ❯ " in plain and "\001" not in plain  # no readline markers

    monkeypatch.setattr(le, "_active", True)
    wrapped = le.colored_prompt("you ❯ ")
    # ANSI codes wrapped in \001..\002 so readline measures width correctly
    assert wrapped.startswith("\001") and "\002" in wrapped and "you ❯ " in wrapped


def test_short_path_collapses_home() -> None:
    from pathlib import Path

    from jarvis_code.cli.chat import _short_path

    assert _short_path(Path.home() / "proj" / "x") == "~/proj/x"
    assert _short_path(Path("/var/tmp/elsewhere")) == "/var/tmp/elsewhere"


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
