"""SystemVoiceEngine — OS text-to-speech (lazy, optional, fully offline).

Two backends, both local and offline:

- macOS: the built-in ``say`` binary, run as a subprocess. This is the reliable
  path on a Mac — it plays from any thread (jarvis speaks from a worker thread,
  where pyttsx3's NSSpeechSynthesizer driver returns silently) and needs no pip
  install.
- Elsewhere: ``pyttsx3`` if it happens to be installed.

Either way only short, curated status strings are spoken. Nothing is sent to a
network service. Speech never crashes the agent — failures degrade to silence.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys

from jarvis_code.utils.logging import get_logger

from .engine import SpeakOptions

_log = get_logger("voice.system")

# Default speaking rate for `say` in words-per-minute; scaled by options.rate.
_BASE_WPM = 175


def _say_path() -> str | None:
    """Path to the macOS ``say`` binary, or None (not macOS / not present)."""
    if sys.platform != "darwin":
        return None
    return shutil.which("say")


def _pyttsx3_available() -> bool:
    try:
        return importlib.util.find_spec("pyttsx3") is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


def system_tts_available() -> bool:
    """True when any offline OS TTS backend is usable (``say`` or pyttsx3)."""
    return _say_path() is not None or _pyttsx3_available()


def say_argv(text: str, options: SpeakOptions, say_bin: str = "say") -> list[str]:
    """Build the ``say`` argv. ``rate`` scales the default ~175 wpm; volume is
    applied with an inline ``[[volm ...]]`` command (``say`` has no volume flag).

    Pure/deterministic so it can be unit-tested without spawning a process.
    """
    wpm = max(1, int(_BASE_WPM * options.rate))
    vol = max(0.0, min(1.0, options.volume))
    # `say` interprets [[...]] as speech commands, so volm is not spoken aloud.
    return [say_bin, "-r", str(wpm), f"[[volm {vol:.2f}]] {text}"]


class SystemVoiceEngine:
    name = "system"

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None

    async def is_available(self) -> bool:
        return system_tts_available()

    async def speak(self, text: str, options: SpeakOptions | None = None) -> None:
        opts = options or SpeakOptions()
        say_bin = _say_path()
        if say_bin is not None:
            await self._speak_say(say_bin, text, opts)
        elif _pyttsx3_available():
            # pyttsx3 is blocking; run it off the event loop.
            await asyncio.to_thread(self._speak_pyttsx3, text, opts)
        else:  # pragma: no cover - no backend
            _log.debug("no OS TTS backend available; skipping speech")

    async def _speak_say(self, say_bin: str, text: str, opts: SpeakOptions) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *say_argv(text, opts, say_bin),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await self._proc.wait()
        except asyncio.CancelledError:
            await self._terminate()
            raise
        except Exception as exc:  # never crash the agent over speech
            _log.warning("say TTS failed (%s); continuing silently", exc)
        finally:
            self._proc = None

    def _speak_pyttsx3(self, text: str, options: SpeakOptions) -> None:  # pragma: no cover
        try:
            import pyttsx3  # type: ignore

            engine = pyttsx3.init()
            engine.setProperty("rate", int(_BASE_WPM * options.rate))
            engine.setProperty("volume", options.volume)
            engine.say(text)
            engine.runAndWait()
        except Exception as exc:  # never crash the agent over speech
            _log.warning("system TTS failed (%s); continuing silently", exc)

    async def stop(self) -> None:
        await self._terminate()
        if _say_path() is None and _pyttsx3_available():  # pragma: no cover
            try:
                import pyttsx3  # type: ignore

                pyttsx3.init().stop()
            except Exception:
                pass

    async def _terminate(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:  # pragma: no cover - already gone
                pass
