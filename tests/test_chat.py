"""Tests for the interactive chat loop (bare `jarvis`)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis_code.agent.loop import RunResult, RunStatus
from jarvis_code.agent.state import RunState
from jarvis_code.cli.chat import chat_loop
from jarvis_code.cli.keyinput import IdleAction, IdleIntent, read_idle_intent
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


# -- single-key idle input (tap Space to talk) ------------------------------


def _intents_from(items):
    it = iter(items)

    def reader(_prompt: str) -> IdleIntent:
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return reader


def test_read_idle_intent_falls_back_to_line_without_tty() -> None:
    # The test runner has no TTY, so this exercises the plain-line fallback path.
    intent = read_idle_intent("plain ", "edit ", input_fn=lambda _p: "hello there")
    assert intent.action is IdleAction.LINE
    assert intent.text == "hello there"


@pytest.mark.asyncio
async def test_chat_key_reader_space_starts_talk() -> None:
    out: list[str] = []
    rt = FakeRuntime()
    mic = FakeMic(transcript="run the tests")
    await chat_loop(
        rt,
        prompt_fn=_prompt_from([]),  # unused: key_reader drives idle reads
        echo=out.append,
        mic=mic,
        stop_waiter=_immediate_stop,
        key_reader=_intents_from(
            [IdleIntent(IdleAction.TALK), IdleIntent(IdleAction.LINE, "/exit")]
        ),
    )
    assert mic.recorded is True
    assert rt.runs == ["run the tests"]  # the spoken transcript became a turn


@pytest.mark.asyncio
async def test_chat_key_reader_none_is_skipped() -> None:
    out: list[str] = []
    rt = FakeRuntime()
    await chat_loop(
        rt,
        prompt_fn=_prompt_from([]),
        echo=out.append,
        key_reader=_intents_from(
            [
                IdleIntent(IdleAction.NONE),  # an ignored key (e.g. a stray arrow)
                IdleIntent(IdleAction.LINE, "do it"),
                IdleIntent(IdleAction.LINE, "/exit"),
            ]
        ),
    )
    assert rt.runs == ["do it"]


@pytest.mark.asyncio
async def test_chat_key_reader_typed_line_still_runs_commands() -> None:
    out: list[str] = []
    rt = FakeRuntime()
    await chat_loop(
        rt,
        prompt_fn=_prompt_from([]),
        echo=out.append,
        key_reader=_intents_from(
            [IdleIntent(IdleAction.LINE, "/help"), IdleIntent(IdleAction.LINE, "/exit")]
        ),
    )
    assert any("Commands:" in o for o in out)  # slash commands work via key_reader too
    assert rt.runs == []


def test_handle_compress_forces_compaction() -> None:
    from jarvis_code.agent import ConversationContext, RunState
    from jarvis_code.cli.chat import _handle_compress

    ctx = ConversationContext("sys", max_chars=10**9, keep_recent=2)
    for i in range(6):
        ctx.add_user(f"m{i}")
    rt = SimpleNamespace(context=ctx, state=RunState(goal="g"))
    out: list[str] = []
    _handle_compress(rt, out.append)
    assert len(ctx.messages) == 3  # compacted to summary + 2 recent
    assert any("compressed" in o for o in out)


@pytest.mark.asyncio
async def test_chat_command_hints_shown_once_not_before_every_prompt() -> None:
    out: list[str] = []
    rt = FakeRuntime()
    await chat_loop(rt, prompt_fn=_prompt_from(["one", "two", "three", "/exit"]), echo=out.append)
    assert sum("/compress" in o for o in out) == 1  # shown under the banner, once


@pytest.mark.asyncio
async def test_mistyped_slash_command_is_not_sent_to_the_model() -> None:
    out: list[str] = []
    rt = FakeRuntime()
    await chat_loop(rt, prompt_fn=_prompt_from(["/hlep", "/exit"]), echo=out.append)
    assert rt.runs == []  # used to become a (token-costing) task
    assert any("unknown command /hlep" in o for o in out)


@pytest.mark.asyncio
async def test_a_message_starting_with_a_path_is_still_a_task() -> None:
    rt = FakeRuntime()
    msg = "/etc/hosts looks wrong, can you check the parser?"
    await chat_loop(rt, prompt_fn=_prompt_from([msg, "/exit"]), echo=lambda _s: None)
    assert rt.runs == [msg]


def test_status_and_clear_commands() -> None:
    from jarvis_code.agent import ConversationContext, RunState
    from jarvis_code.cli.chat import _handle_clear, _handle_status

    ctx = ConversationContext("sys", max_chars=1000)
    ctx.add_user("x" * 500)
    rt = SimpleNamespace(
        context=ctx,
        state=RunState(files_changed=["a.py"]),
        provider=SimpleNamespace(name="anthropic", model="claude-x"),
        executor=SimpleNamespace(
            permissions=SimpleNamespace(mode=PermissionMode.PLAN),
            ctx=SimpleNamespace(sandbox=SimpleNamespace(root="/tmp/proj")),
        ),
    )
    out: list[str] = []
    _handle_status(rt, out.append)
    blob = "\n".join(out)
    assert "plan" in blob and "anthropic · claude-x" in blob and "/tmp/proj" in blob
    assert "50%" in blob and "a.py" in blob

    _handle_clear(rt, out.append)
    assert ctx.messages == []
    assert any("cleared" in o for o in out)


@pytest.mark.asyncio
async def test_failed_turn_tells_the_user_why() -> None:
    # The real runtime RETURNS an error result (it doesn't raise); the chat used
    # to print only "[error]" and drop the provider's message on the floor.
    class ErroringRuntime(FakeRuntime):
        async def run(self, message: str) -> RunResult:
            return RunResult(RunStatus.ERROR, "anthropic API error 401: invalid x-api-key", 1, RunState())

    out: list[str] = []
    await chat_loop(ErroringRuntime(), prompt_fn=_prompt_from(["hi", "/exit"]), echo=out.append)
    assert any("invalid x-api-key" in o for o in out)


@pytest.mark.asyncio
async def test_chat_mic_command_cycles_mode() -> None:
    out: list[str] = []
    rt = FakeRuntime()

    class CyclingMic(FakeMic):
        def __init__(self) -> None:
            super().__init__()
            self.cycled = 0

        def cycle_mode(self):
            self.cycled += 1
            return None

    mic = CyclingMic()
    await chat_loop(rt, prompt_fn=_prompt_from(["/mic", "/exit"]), echo=out.append, mic=mic)
    assert mic.cycled == 1
    assert rt.runs == []


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


# -- the first key typed at the voice prompt must never be lost ---------------


class _FakeReadline:
    """A readline whose pre-input hook either fires (GNU) or never does (libedit)."""

    __doc__ = "fake"

    def __init__(self, fires: bool) -> None:
        self.fires = fires
        self.buffer = ""
        self.hook = None

    def set_pre_input_hook(self, hook=None) -> None:
        self.hook = hook

    def insert_text(self, text: str) -> None:
        self.buffer += text

    def redisplay(self) -> None: ...

    def get_line_buffer(self) -> str:
        return self.buffer


@pytest.mark.parametrize("hook_fires", [True, False])
def test_first_key_survives_whether_or_not_the_readline_hook_fires(monkeypatch, hook_fires) -> None:
    import sys

    from jarvis_code.cli.keyinput import _input_with_prefix

    fake = _FakeReadline(hook_fires)
    monkeypatch.setitem(sys.modules, "readline", fake)

    def editor(_prompt: str) -> str:
        # A real line editor calls the hook (GNU readline) then returns its
        # buffer plus what the user typed. libedit never calls it.
        if fake.fires and fake.hook is not None:
            fake.hook()
        return fake.buffer + "ello"

    # Typed "h" at the key prompt, then "ello": the message is "hello" either way.
    # (On libedit the hook never ran, and the "h" used to be dropped: "ello".)
    assert _input_with_prefix("you ❯ ", "h", editor) == "hello"
    assert fake.hook is None  # the hook is always uninstalled afterwards


def test_push_back_reports_failure_instead_of_raising(monkeypatch) -> None:
    from jarvis_code.cli import keyinput

    monkeypatch.setattr(keyinput, "_pushback_works", None)
    assert keyinput._push_back(-1, b"h") is False  # bad fd -> OSError -> False
    assert keyinput._pushback_works is False  # remembered: not retried per key


def test_key_strategy_defaults_and_override(monkeypatch) -> None:
    from jarvis_code.cli import keyinput

    monkeypatch.delenv("JARVIS_KEY_PUSHBACK", raising=False)
    monkeypatch.setattr(keyinput, "_is_libedit", lambda: True)
    assert keyinput._strategy() == "pushback"  # macOS libedit: the hook never fires
    monkeypatch.setattr(keyinput, "_is_libedit", lambda: False)
    assert keyinput._strategy() == "hook"
    monkeypatch.setenv("JARVIS_KEY_PUSHBACK", "prefix")
    assert keyinput._strategy() == "prefix"
