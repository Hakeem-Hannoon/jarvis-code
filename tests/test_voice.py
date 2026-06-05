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
    assert vmsg.tool_status_phrase("run_shell") == "Executing your command."
    assert vmsg.tool_status_phrase("read_file") == "Accessing the file."
    assert vmsg.tool_status_phrase("search_files") == "Sweeping the codebase."
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


def test_speakable_strips_symbol_names_keeps_words() -> None:
    assert vmsg.speakable("src/cli/app.py") == "src cli app.py"  # no "slash"
    assert vmsg.speakable("**done** now") == "done now"          # no "asterisk"
    assert vmsg.speakable("test_helper|x") == "test helper x"    # no underscore/pipe
    assert vmsg.speakable("Reading the file.") == "Reading the file."  # prose kept


def test_spoken_text_has_no_symbol_names() -> None:
    # The redaction marker must never be voiced as "asterisk asterisk asterisk".
    jlog.register_secret("sk-spoken-9999999999")
    out = vmsg.shorten("deploying with sk-spoken-9999999999 now")
    assert "*" not in out and "sk-spoken" not in out and "asterisk" not in out
    # A path-y status loses its slashes when spoken.
    assert "/" not in vmsg.shorten("touching src/cli/chat.py")


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


def test_say_argv_includes_voice_when_given() -> None:
    argv = say_argv("Hi there.", SpeakOptions(), say_bin="say", voice="Daniel")
    assert argv[:3] == ["say", "-v", "Daniel"]
    assert argv[-1].endswith("Hi there.")
    # no voice -> no -v flag
    assert "-v" not in say_argv("Hi.", SpeakOptions(), say_bin="say")


def test_say_argv_pitch_and_robotic_only_when_set() -> None:
    # Defaults: no inline pitch/modulation commands (unchanged baseline behavior).
    base = say_argv("Hi.", SpeakOptions(), say_bin="say")
    assert "[[pbas" not in base[-1] and "[[pmod" not in base[-1]
    # Lowered pitch + a robotic (flatter) delivery inject the inline `say` commands.
    styled = say_argv("Hi.", SpeakOptions(pitch=0.8, robotic=0.5), say_bin="say")
    assert "[[pbas" in styled[-1] and "[[pmod" in styled[-1]
    assert styled[-1].endswith(" Hi.")  # the words themselves are unchanged


def test_resolve_say_voice_maps_jarvis_preset(monkeypatch) -> None:
    monkeypatch.setattr(vsystem, "_available_say_voices", lambda: {"Daniel", "Alex"})
    assert vsystem._resolve_say_voice("jarvis-inspired") == "Daniel"  # JARVIS -> British male
    assert vsystem._resolve_say_voice("Alex") == "Alex"               # real voice passes through
    assert vsystem._resolve_say_voice("Nope") is None                 # unknown -> no -v
    # if no candidate is installed, degrade rather than pass an invalid -v
    monkeypatch.setattr(vsystem, "_available_say_voices", lambda: {"Zoe"})
    assert vsystem._resolve_say_voice("jarvis-inspired") is None


def test_resolve_say_voice_prefers_premium(monkeypatch) -> None:
    # The natural neural variant wins over the robotic compact voice.
    monkeypatch.setattr(
        vsystem, "_available_say_voices", lambda: {"Daniel", "Daniel (Premium)", "Arthur"}
    )
    assert vsystem._resolve_say_voice("jarvis-inspired") == "Daniel (Premium)"
    monkeypatch.setattr(vsystem, "_available_say_voices", lambda: {"Alex", "Alex (Enhanced)"})
    assert vsystem._resolve_say_voice("Alex") == "Alex (Enhanced)"


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
    assert engine.spoken == ["Executing your command.", "Running diagnostics."]


def test_subsystem_options_carry_voice_character() -> None:
    vs = VoiceSubsystem(FakeEngine(), VoiceConfig(pitch=0.8, robotic=0.4))
    assert vs.options.pitch == 0.8
    assert vs.options.robotic == 0.4


def test_speech_rate_default_is_brisk_and_capped_at_3x() -> None:
    from pydantic import ValidationError

    assert VoiceConfig().speech_rate == 1.25  # brisk default
    assert VoiceConfig(speech_rate=3.0).speech_rate == 3.0  # editable up to 3x
    with pytest.raises(ValidationError):
        VoiceConfig(speech_rate=3.5)  # beyond 3x is rejected


def test_system_prompt_narration_gated_on_opt_in() -> None:
    from jarvis_code.agent.prompts import build_system_prompt
    from jarvis_code.permissions import PermissionMode

    common = dict(mode=PermissionMode.AUTO, sandbox_root="/x", tool_specs=[], memory=None)
    assert "spoken aloud" not in build_system_prompt(**common)
    assert "spoken aloud" in build_system_prompt(**common, narrate=True)


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


def test_clean_intent_brief_capped_and_redacted() -> None:
    jlog.register_secret("sk-intentleak-123")
    out = vmsg.clean_intent(
        "Running with sk-intentleak-123 now. Then I verify the build. And a third part."
    )
    assert out is not None
    assert "sk-intentleak-123" not in out      # secret redacted
    assert "verify" in out                      # keeps up to two sentences (the why)
    assert "third part" not in out.lower()      # but never a third
    assert vmsg.clean_intent("   ") is None
    assert vmsg.clean_intent(None) is None
    assert len(vmsg.clean_intent("word " * 100)) <= 201  # length capped


def test_specific_action_phrase_uses_program_name_for_shell() -> None:
    assert vmsg.specific_action_phrase("run_shell", "git status -sb") == "Executing the git command."
    assert vmsg.specific_action_phrase("run_shell", "") == "Executing your command."
    # argument values are never voiced — only the program name
    assert "status" not in vmsg.specific_action_phrase("run_shell", "git status -sb")


def test_specific_action_phrase_names_file_by_basename() -> None:
    assert vmsg.specific_action_phrase("write_file", "src/pkg/config.toml") == "Writing config.toml."
    assert vmsg.specific_action_phrase("read_file", "/proj/loop.py") == "Accessing loop.py."
    assert vmsg.specific_action_phrase("edit_file", "a/b/main.py") == "Modifying main.py."
    # only the basename is spoken — never the directory tree
    out = vmsg.specific_action_phrase("read_file", "/secret/dir/notes.md")
    assert "secret" not in out and out == "Accessing notes.md."
    # no path -> generic fallback, never a crash
    assert vmsg.specific_action_phrase("write_file", "") == "Writing to disk."


def test_action_phrase_prefers_intent_then_falls_back() -> None:
    assert vmsg.action_phrase("run_shell", "pip install x", intent="Installing the deps.") == (
        "Installing the deps"
    )
    assert vmsg.action_phrase("run_shell", "git status", intent=None) == "Executing the git command."
    # file ops also speak the model's reasoning when present (not just the name)
    assert vmsg.action_phrase("write_file", "config.toml", intent="Wiring up the config loader.") == (
        "Wiring up the config loader"
    )
    # ...and fall back to the filename only when there is no reason
    assert vmsg.action_phrase("write_file", "config.toml", intent=None) == "Writing config.toml."


@pytest.mark.asyncio
async def test_reporter_speaks_model_intent_once_when_opted_in() -> None:
    engine = FakeEngine()
    vs = VoiceSubsystem(engine, VoiceConfig(speak_intent=True))
    await vs.start()
    reporter = VoiceReporter(vs)

    reporter.assistant_text("I'll install the project dependencies so the tests run.")
    reporter.tool_started("run_shell", "pip install -e .")  # speaks the intent
    reporter.tool_started("read_file", "/proj/x.py")  # same turn -> stays quiet
    await vs.drain()
    await vs.aclose()

    # Exactly ONE line per turn: the reason, and nothing for the second tool.
    assert engine.spoken == ["I'll install the project dependencies so the tests run"]
    # The raw command arguments were never voiced.
    assert all("pip install -e ." not in s for s in engine.spoken)


@pytest.mark.asyncio
async def test_reporter_speaks_once_per_turn_not_per_tool() -> None:
    # "Talking too much" fix: a batch of tool calls in one model turn yields one
    # spoken line (the reason), not one per tool. A new turn speaks again.
    engine = FakeEngine()
    vs = VoiceSubsystem(engine, VoiceConfig(speak_intent=True))
    await vs.start()
    reporter = VoiceReporter(vs)

    reporter.assistant_text("Refactoring the parser to handle nested calls.")
    for name, summary in [("read_file", "a.py"), ("edit_file", "b.py"), ("write_file", "c.py")]:
        reporter.tool_started(name, summary)

    reporter.assistant_text("Now wiring the new function into the loader.")
    reporter.tool_started("edit_file", "loader.py")
    reporter.tool_started("write_file", "loader.py")
    await vs.drain()
    await vs.aclose()

    assert engine.spoken == [
        "Refactoring the parser to handle nested calls",
        "Now wiring the new function into the loader",
    ]


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

    assert engine.spoken == ["Accessing the file."]  # model text never spoken


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
    assert "Accessing the file." in engine.spoken
    assert "Understood. Rerouting." in engine.spoken
