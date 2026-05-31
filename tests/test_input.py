"""Phase 8 voice-input tests: mode switching, Muted guarantee, cloud gating."""

from __future__ import annotations

import pytest

from jarvis_code.config import MicMode, SttEngineName, VoiceInputConfig
from jarvis_code.input import (
    CloudSTTNotAllowed,
    MicController,
    NoopSTT,
    build_stt_engine,
    render_mic_bar,
    select_stt,
)


class SpyRecorder:
    """Records whether record() was ever called and returns dummy audio."""

    def __init__(self, available: bool = True) -> None:
        self._available = available
        self.record_calls = 0

    async def is_available(self) -> bool:
        return self._available

    async def record(self, max_seconds: float) -> bytes:
        self.record_calls += 1
        return b"\x00\x01" * 16  # dummy PCM; never persisted

    # convenience for raising if ever touched in muted mode
    @property
    def touched(self) -> bool:
        return self.record_calls > 0


class FakeSTT:
    name = "fake"
    is_cloud = False

    def __init__(self, text: str = "run the tests") -> None:
        self.text = text
        self.seen_audio: list[bytes] = []

    async def is_available(self) -> bool:
        return True

    async def transcribe(self, audio: bytes) -> str:
        self.seen_audio.append(audio)
        return self.text


def _cfg(**kw) -> VoiceInputConfig:
    base = {"enabled": True, "mode": MicMode.OPEN_MIC}
    base.update(kw)
    return VoiceInputConfig(**base)


# -- mode switching ---------------------------------------------------------


def test_cycle_mode_open_ptt_muted() -> None:
    mc = MicController(_cfg(mode=MicMode.OPEN_MIC), NoopSTT())
    assert mc.mode == MicMode.OPEN_MIC
    assert mc.cycle_mode() == MicMode.PUSH_TO_TALK
    assert mc.cycle_mode() == MicMode.MUTED
    assert mc.cycle_mode() == MicMode.OPEN_MIC


def test_set_mode_emits_event() -> None:
    seen: list[MicMode] = []
    mc = MicController(_cfg(), NoopSTT(), on_mode_change=seen.append)
    mc.set_mode(MicMode.MUTED)
    mc.set_mode(MicMode.PUSH_TO_TALK)
    assert seen == [MicMode.MUTED, MicMode.PUSH_TO_TALK]


def test_set_same_mode_is_noop() -> None:
    seen: list[MicMode] = []
    mc = MicController(_cfg(mode=MicMode.MUTED), NoopSTT(), on_mode_change=seen.append)
    mc.set_mode(MicMode.MUTED)
    assert seen == []


def test_is_listening_reflects_mode_and_enabled() -> None:
    assert MicController(_cfg(mode=MicMode.OPEN_MIC), NoopSTT()).is_listening() is True
    assert MicController(_cfg(mode=MicMode.MUTED), NoopSTT()).is_listening() is False
    assert MicController(_cfg(enabled=False), NoopSTT()).is_listening() is False


def test_mic_bar_highlights_current() -> None:
    bar = render_mic_bar(MicMode.PUSH_TO_TALK)
    assert "Mic:" in bar
    assert "[>Push to Talk<]" in bar
    assert "[Open Mic]" in bar and "[Muted]" in bar


# -- Muted guarantees nothing is captured -----------------------------------


@pytest.mark.asyncio
async def test_muted_never_records_or_transcribes() -> None:
    recorder = SpyRecorder()
    stt = FakeSTT()
    mc = MicController(_cfg(mode=MicMode.MUTED), stt, recorder)
    result = await mc.capture()
    assert result is None
    assert recorder.touched is False  # recorder never invoked
    assert stt.seen_audio == []  # nothing transcribed
    assert mc.last_text is None  # nothing stored


@pytest.mark.asyncio
async def test_ptt_in_muted_does_nothing() -> None:
    recorder = SpyRecorder()
    mc = MicController(_cfg(mode=MicMode.MUTED), FakeSTT(), recorder)
    await mc.ptt_press()
    assert await mc.ptt_release() is None
    assert recorder.touched is False


# -- capture (open mic / PTT) -----------------------------------------------


@pytest.mark.asyncio
async def test_open_mic_capture_returns_text_only() -> None:
    recorder = SpyRecorder()
    stt = FakeSTT("open the file")
    mc = MicController(_cfg(mode=MicMode.OPEN_MIC), stt, recorder)
    text = await mc.capture()
    assert text == "open the file"
    assert recorder.touched is True
    assert mc.last_text == "open the file"  # text stored, not audio
    assert isinstance(mc.last_text, str)


@pytest.mark.asyncio
async def test_ptt_press_release_transcribes() -> None:
    recorder = SpyRecorder()
    mc = MicController(_cfg(mode=MicMode.PUSH_TO_TALK), FakeSTT("commit the change"), recorder)
    await mc.ptt_press()
    text = await mc.ptt_release()
    assert text == "commit the change"


@pytest.mark.asyncio
async def test_capture_degrades_when_no_mic_or_stt() -> None:
    # No recorder hardware -> returns None, no crash.
    mc = MicController(_cfg(mode=MicMode.OPEN_MIC), FakeSTT(), SpyRecorder(available=False))
    assert await mc.capture() is None
    # No STT installed -> NoopSTT not available -> returns None.
    mc2 = MicController(_cfg(mode=MicMode.OPEN_MIC), NoopSTT(), SpyRecorder())
    assert await mc2.capture() is None


# -- cloud STT is opt-in only -----------------------------------------------


def test_cloud_stt_refused_without_opt_in() -> None:
    with pytest.raises(CloudSTTNotAllowed):
        build_stt_engine("cloud", allow_cloud=False)


def test_cloud_stt_allowed_with_opt_in() -> None:
    engine = build_stt_engine("cloud", allow_cloud=True)
    assert engine.is_cloud is True


def test_select_stt_local_default_is_not_cloud() -> None:
    cfg = _cfg(stt_engine=SttEngineName.FASTER_WHISPER, allow_cloud_stt=False)
    engine = select_stt(cfg)
    assert engine.is_cloud is False


def test_select_stt_falls_back_to_noop_on_cloud_refusal(monkeypatch) -> None:
    # If a cloud engine were configured without opt-in, select_stt must catch
    # the refusal and degrade to NoopSTT rather than raise.
    import jarvis_code.input.factory as factory

    def _raise(name, *, allow_cloud, model=""):
        raise CloudSTTNotAllowed("nope")

    monkeypatch.setattr(factory, "build_stt_engine", _raise)
    engine = select_stt(_cfg(allow_cloud_stt=False))
    assert isinstance(engine, NoopSTT)


# -- transcripts never contain audio ----------------------------------------


@pytest.mark.asyncio
async def test_transcript_callback_receives_text_not_audio() -> None:
    captured_for_log: list = []

    recorder = SpyRecorder()
    stt = FakeSTT("deploy to staging")
    mc = MicController(_cfg(mode=MicMode.OPEN_MIC), stt, recorder)
    text = await mc.capture()

    # A continuity log would store only the text instruction.
    if text:
        captured_for_log.append(text)
    assert captured_for_log == ["deploy to staging"]
    assert all(isinstance(x, str) for x in captured_for_log)
    # The controller exposes no audio buffer.
    assert not hasattr(mc, "audio_buffer")
    assert mc.stores_audio is False
