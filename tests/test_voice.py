"""Phase 7 voice-output tests: curated messages, engine fallback, no secrets."""

from __future__ import annotations

import pytest

from jarvis_code.config import VoiceConfig, VoiceEngineName
from jarvis_code.utils import logging as jlog
from jarvis_code.voice import (
    NoopVoiceEngine,
    SpeakOptions,
    SystemVoiceEngine,
    VoiceReporter,
    VoiceSubsystem,
    engine_available,
    say_argv,
    select_engine,
)
from jarvis_code.voice import messages as vmsg
from jarvis_code.voice import system as vsystem


@pytest.fixture(autouse=True)
def _clean_secrets():
    jlog.clear_secrets()
    yield
    jlog.clear_secrets()


class FakeEngine:
    name = "fake"

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.stopped = False

    async def speak(self, text: str, options: SpeakOptions | None = None) -> None:
        self.spoken.append(text)

    async def stop(self) -> None:
        self.stopped = True

    async def is_available(self) -> bool:
        return True


# -- curated message generation ---------------------------------------------


def test_tool_status_phrases_are_curated() -> None:
    assert vmsg.tool_status_phrase("run_shell") == "Running a command."
    assert vmsg.tool_status_phrase("read_file") == "Reading a file."
    assert vmsg.tool_status_phrase("search_files") == "Searching the project."
    # all phrases are short
    for tool in ("list_files", "edit_file", "git_diff", "spawn_subagent"):
        assert len(vmsg.tool_status_phrase(tool)) <= 60


def test_unknown_tool_has_no_phrase() -> None:
    assert vmsg.tool_status_phrase("definitely_not_a_tool") is None


def test_permission_phrase_drops_command_arguments() -> None:
    # A command carrying a secret-looking token must not be spoken verbatim.
    phrase = vmsg.permission_request_phrase("export TOKEN=sk-supersecret-abc make deploy")
    assert "export" in phrase
    assert "sk-supersecret-abc" not in phrase
    assert "TOKEN" not in phrase  # only the program name is spoken


def test_shorten_caps_and_redacts() -> None:
    jlog.register_secret("sk-leak-1234567890")
    out = vmsg.shorten("status with sk-leak-1234567890 inside", max_chars=160)
    assert "sk-leak-1234567890" not in out
    long = vmsg.shorten("word " * 100, max_chars=40)
    assert len(long) <= 41


# -- engine selection + fallback --------------------------------------------


def _voice(engine: VoiceEngineName, fallback: VoiceEngineName) -> VoiceConfig:
    return VoiceConfig(engine=engine, fallback_engine=fallback)


def test_noop_always_available() -> None:
    assert engine_available(VoiceEngineName.NOOP, VoiceConfig()) is True


def test_piper_unavailable_without_model_path() -> None:
    # No model_path configured -> piper cannot be selected.
    assert engine_available(VoiceEngineName.PIPER, VoiceConfig(model_path="")) is False


def test_select_falls_back_to_noop_when_nothing_installed() -> None:
    # In the test environment piper/pyttsx3 aren't installed, so selection of a
    # piper-configured voice degrades cleanly to Noop (never crashes).
    engine = select_engine(_voice(VoiceEngineName.PIPER, VoiceEngineName.SYSTEM))
    assert engine.name in ("piper", "system", "noop")
    if not engine_available(VoiceEngineName.SYSTEM, VoiceConfig()):
        assert engine.name == "noop"


def test_select_noop_when_configured() -> None:
    engine = select_engine(_voice(VoiceEngineName.NOOP, VoiceEngineName.NOOP))
    assert isinstance(engine, NoopVoiceEngine)


# -- system engine: macOS `say` backend -------------------------------------


def test_say_argv_scales_rate_and_sets_volume() -> None:
    argv = say_argv("Reading a file.", SpeakOptions(rate=2.0, volume=0.5), say_bin="say")
    assert argv[0] == "say"
    assert argv[1:3] == ["-r", "350"]  # 175 wpm * 2.0
    # The spoken text is one argument carrying an inline (un-spoken) volume cmd.
    assert argv[-1] == "[[volm 0.50]] Reading a file."


def test_system_available_when_say_present(monkeypatch) -> None:
    monkeypatch.setattr(vsystem, "_say_path", lambda: "/usr/bin/say")
    monkeypatch.setattr(vsystem, "_pyttsx3_available", lambda: False)
    assert vsystem.system_tts_available() is True
    assert engine_available(VoiceEngineName.SYSTEM, VoiceConfig()) is True


def test_system_unavailable_when_no_backend(monkeypatch) -> None:
    monkeypatch.setattr(vsystem, "_say_path", lambda: None)
    monkeypatch.setattr(vsystem, "_pyttsx3_available", lambda: False)
    assert vsystem.system_tts_available() is False
    assert engine_available(VoiceEngineName.SYSTEM, VoiceConfig()) is False


@pytest.mark.asyncio
async def test_speak_invokes_say_subprocess(monkeypatch) -> None:
    monkeypatch.setattr(vsystem, "_say_path", lambda: "/usr/bin/say")
    calls: list[list[str]] = []

    class _FakeProc:
        returncode = 0

        async def wait(self) -> int:
            return 0

        def terminate(self) -> None:  # pragma: no cover - not hit here
            pass

    async def _fake_exec(*argv, **kwargs):
        calls.append(list(argv))
        return _FakeProc()

    monkeypatch.setattr(vsystem.asyncio, "create_subprocess_exec", _fake_exec)
    engine = SystemVoiceEngine()
    await engine.speak("Running a command.", SpeakOptions())
    assert calls and calls[0][0] == "/usr/bin/say"
    assert calls[0][-1].endswith("Running a command.")


@pytest.mark.asyncio
async def test_stop_terminates_running_say(monkeypatch) -> None:
    monkeypatch.setattr(vsystem, "_say_path", lambda: "/usr/bin/say")
    terminated = {"called": False}

    class _FakeProc:
        returncode = None

        async def wait(self) -> int:  # pragma: no cover - not awaited in this test
            return 0

        def terminate(self) -> None:
            terminated["called"] = True
            self.returncode = -15

    engine = SystemVoiceEngine()
    engine._proc = _FakeProc()  # simulate an in-progress utterance
    await engine.stop()
    assert terminated["called"] is True


# -- queue: speak, drain, cancel --------------------------------------------


@pytest.mark.asyncio
async def test_subsystem_speaks_curated_and_drains() -> None:
    engine = FakeEngine()
    vs = VoiceSubsystem(engine, VoiceConfig())
    await vs.start()
    vs.announce_tool("run_shell")
    vs.announce_status("running_tests")
    await vs.drain()
    await vs.aclose()
    assert engine.spoken == ["Running a command.", "Running the test suite."]


@pytest.mark.asyncio
async def test_stop_clears_pending_and_stops_engine() -> None:
    engine = FakeEngine()
    vs = VoiceSubsystem(engine, VoiceConfig())
    # don't start the worker, so items stay queued
    vs.say("Scanning the project.")
    vs.say("Reading a file.")
    await vs.stop()
    assert engine.stopped is True
    assert vs._queue.empty()


# -- descriptive narration: opt-in model-authored intent --------------------


def test_clean_intent_first_sentence_capped_and_redacted() -> None:
    jlog.register_secret("sk-intentleak-123")
    out = vmsg.clean_intent("Running with sk-intentleak-123 now. Second sentence here.")
    assert out is not None
    assert "sk-intentleak-123" not in out
    assert "Second sentence" not in out  # only the first sentence
    assert vmsg.clean_intent("   ") is None
    assert vmsg.clean_intent(None) is None
    assert len(vmsg.clean_intent("word " * 100)) <= 141


def test_specific_action_phrase_uses_program_name_for_shell() -> None:
    assert vmsg.specific_action_phrase("run_shell", "git status -sb") == "Running the git command."
    assert vmsg.specific_action_phrase("run_shell", "") == "Running a command."
    # argument values are never voiced — only the program name
    assert "status" not in vmsg.specific_action_phrase("run_shell", "git status -sb")
    assert vmsg.specific_action_phrase("read_file", "/x/y") == "Reading a file."


def test_action_phrase_prefers_intent_then_falls_back() -> None:
    assert vmsg.action_phrase("run_shell", "pip install x", intent="Installing the deps.") == (
        "Installing the deps"
    )
    assert vmsg.action_phrase("run_shell", "git status", intent=None) == "Running the git command."


@pytest.mark.asyncio
async def test_reporter_speaks_model_intent_once_when_opted_in() -> None:
    engine = FakeEngine()
    vs = VoiceSubsystem(engine, VoiceConfig(speak_intent=True))
    await vs.start()
    reporter = VoiceReporter(vs)

    reporter.assistant_text("I'll install the project dependencies so the tests run.")
    reporter.tool_started("run_shell", "pip install -e .")  # speaks the intent
    reporter.tool_started("read_file", "/proj/x.py")  # no new intent -> curated
    await vs.drain()
    await vs.aclose()

    assert engine.spoken[0] == "I'll install the project dependencies so the tests run"
    assert engine.spoken[1] == "Reading a file."
    # The raw command arguments were never voiced.
    assert all("pip install -e ." not in s for s in engine.spoken)


@pytest.mark.asyncio
async def test_reporter_intent_off_by_default_stays_curated() -> None:
    engine = FakeEngine()
    vs = VoiceSubsystem(engine, VoiceConfig())  # speak_intent defaults False
    await vs.start()
    reporter = VoiceReporter(vs)

    reporter.assistant_text("I'll read the config to understand the layout.")
    reporter.tool_started("read_file", "/proj/config.toml")
    await vs.drain()
    await vs.aclose()

    assert engine.spoken == ["Reading a file."]  # model text never spoken


@pytest.mark.asyncio
async def test_reporter_intent_redacts_secret() -> None:
    secret = "sk-spokenintent-9876543210"
    jlog.register_secret(secret)
    engine = FakeEngine()
    vs = VoiceSubsystem(engine, VoiceConfig(speak_intent=True))
    await vs.start()
    reporter = VoiceReporter(vs)

    reporter.assistant_text(f"Calling the API with {secret} to deploy.")
    reporter.tool_started("run_shell", "curl https://x")
    await vs.drain()
    await vs.aclose()

    assert all(secret not in s for s in engine.spoken)


# -- the privacy invariant: no file contents / secrets reach the engine -----


@pytest.mark.asyncio
async def test_no_file_contents_or_secrets_reach_engine() -> None:
    secret = "sk-voiceleak-1234567890"
    jlog.register_secret(secret)
    engine = FakeEngine()
    vs = VoiceSubsystem(engine, VoiceConfig())
    await vs.start()

    # Simulate the agent finishing a tool whose OUTPUT contains a secret + file
    # contents. The voice layer only ever sees the tool name.
    reporter = VoiceReporter(vs)
    reporter.tool_started("read_file", summary="/etc/anything")
    reporter.assistant_text(f"Here is the file contents and key {secret} ...")
    reporter.permission("run_shell", "denied", "plan mode")
    await vs.drain()
    await vs.aclose()

    joined = " ".join(engine.spoken)
    assert secret not in joined
    assert "file contents" not in joined
    # only curated phrases were spoken
    assert "Reading a file." in engine.spoken
    assert "Permission denied. Choosing another route." in engine.spoken
