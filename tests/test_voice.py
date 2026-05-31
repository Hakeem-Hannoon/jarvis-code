"""Phase 7 voice-output tests: curated messages, engine fallback, no secrets."""

from __future__ import annotations

import pytest

from jarvis_code.config import VoiceConfig, VoiceEngineName
from jarvis_code.utils import logging as jlog
from jarvis_code.voice import (
    NoopVoiceEngine,
    SpeakOptions,
    VoiceReporter,
    VoiceSubsystem,
    engine_available,
    select_engine,
)
from jarvis_code.voice import messages as vmsg


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
