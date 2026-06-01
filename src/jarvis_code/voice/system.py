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
import re
import shutil
import sys

from jarvis_code.utils.logging import get_logger

from .engine import SpeakOptions

_log = get_logger("voice.system")

# Default speaking rate for `say` in words-per-minute; scaled by options.rate.
_BASE_WPM = 175

# The JARVIS-style preset prefers natural-sounding British male voices, best
# first. The "(Premium)"/"(Enhanced)" variants are macOS neural voices and sound
# far more realistic than the compact defaults; the plain names are the robotic
# fallbacks. This approximates the Iron Man assistant style — it is NOT a clone
# of any real actor's voice.
_JARVIS_VOICE_PRIORITY: list[str] = [
    "Oliver (Premium)", "Daniel (Premium)", "Arthur (Premium)", "Jamie (Premium)",
    "Oliver (Enhanced)", "Daniel (Enhanced)", "Arthur (Enhanced)",
    "Oliver", "Arthur", "Daniel",
]
_PRESET_CANDIDATES: dict[str, list[str]] = {
    "jarvis-inspired": _JARVIS_VOICE_PRIORITY,
    "jarvis": _JARVIS_VOICE_PRIORITY,
}

_say_voices_cache: set[str] | None = None


def _say_path() -> str | None:
    """Path to the macOS ``say`` binary, or None (not macOS / not present)."""
    if sys.platform != "darwin":
        return None
    return shutil.which("say")


def _available_say_voices() -> set[str]:
    """Names of installed `say` voices (first token of each line), cached.

    Used only to validate a resolved voice so we never pass `say` an unknown
    `-v` (which would error). Failures degrade to an empty set (no `-v`).
    """
    global _say_voices_cache
    if _say_voices_cache is not None:
        return _say_voices_cache
    voices: set[str] = set()
    say_bin = _say_path()
    if say_bin is not None:
        try:
            import subprocess

            out = subprocess.run(
                [say_bin, "-v", "?"], capture_output=True, text=True, timeout=5
            ).stdout
            for line in out.splitlines():
                # Format: "Daniel (Premium)    en_GB    # Hello!...". The full name
                # (incl. any "(Premium)"/"(Enhanced)" qualifier) is the -v value, so
                # keep everything before the 2+ space gap preceding the lang code.
                m = re.match(r"^(.*?)\s{2,}\S", line)
                name = (m.group(1) if m else line).strip()
                if name:
                    voices.add(name)
        except Exception:  # pragma: no cover - say listing is best effort
            voices = set()
    _say_voices_cache = voices
    return voices


def active_say_voice(preset: str | None) -> str:
    """Human-readable name of the `say` voice that will be used for ``preset``."""
    return _resolve_say_voice(preset) or "system default"


def _resolve_say_voice(preset: str | None) -> str | None:
    """Map a voice preset/name to the best installed `say` voice, or None.

    Presets expand to a priority list (premium/enhanced neural voices first). A
    concrete voice name is honored, preferring its premium/enhanced variant if
    installed. Returns None when nothing matches (so no invalid ``-v`` is passed).
    """
    p = (preset or "").strip()
    avail = _available_say_voices()
    for candidate in _PRESET_CANDIDATES.get(p, []):
        if candidate in avail:
            return candidate
    if p:
        for variant in (f"{p} (Premium)", f"{p} (Enhanced)", p):
            if variant in avail:
                return variant
    return None


def _pyttsx3_available() -> bool:
    try:
        return importlib.util.find_spec("pyttsx3") is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


def system_tts_available() -> bool:
    """True when any offline OS TTS backend is usable (``say`` or pyttsx3)."""
    return _say_path() is not None or _pyttsx3_available()


def say_argv(
    text: str, options: SpeakOptions, say_bin: str = "say", voice: str | None = None
) -> list[str]:
    """Build the ``say`` argv. ``rate`` scales the default ~175 wpm; volume is
    applied with an inline ``[[volm ...]]`` command (``say`` has no volume flag);
    ``voice`` (when given) selects a concrete `say` voice via ``-v``.

    Pure/deterministic so it can be unit-tested without spawning a process.
    """
    wpm = max(1, int(_BASE_WPM * options.rate))
    vol = max(0.0, min(1.0, options.volume))
    argv = [say_bin]
    if voice:
        argv += ["-v", voice]
    argv += ["-r", str(wpm)]
    # `say` interprets [[...]] as speech commands, so volm is not spoken aloud.
    argv.append(f"[[volm {vol:.2f}]] {text}")
    return argv


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
            voice = _resolve_say_voice(opts.voice)
            self._proc = await asyncio.create_subprocess_exec(
                *say_argv(text, opts, say_bin, voice),
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
