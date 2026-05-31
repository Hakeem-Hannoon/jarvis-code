"""`jarvis voices list` / `jarvis voices test`.

Phase 6 ships a graceful, non-crashing version that reports configured engines
and availability. Phase 7 wires the real local TTS engines (Piper/System/Noop)
behind the same commands.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import typer

from jarvis_code.config import ConfigError, find_config_path, load_config

from .options import GlobalOptions


def _have(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


def _load_voice_config(opts: GlobalOptions):
    base = Path(opts.sandbox) if opts.sandbox else Path.cwd()
    cfg_path = find_config_path(base)
    if cfg_path is None:
        return None
    try:
        return load_config(cfg_path).voice
    except ConfigError:
        return None


def voices_list(opts: GlobalOptions) -> int:
    typer.echo(typer.style("Voice engines", bold=True))
    typer.echo(f"  piper (local neural TTS): {'available' if _have('piper') else 'not installed'}")
    typer.echo(f"  system (pyttsx3):         {'available' if _have('pyttsx3') else 'not installed'}")
    typer.echo("  noop (silent fallback):   always available")

    voice = _load_voice_config(opts)
    if voice is not None:
        typer.echo("")
        typer.echo(f"Configured engine:   {voice.engine.value}")
        typer.echo(f"Fallback engine:     {voice.fallback_engine.value}")
        typer.echo(f"Default voice preset: {voice.default_voice}")
        typer.echo(f"Voice output enabled: {voice.enabled}")
    else:
        typer.echo("")
        typer.echo("No config found (run: jarvis init). Showing engine availability only.")
    return 0


def voices_test(opts: GlobalOptions) -> int:
    phrase = "Voice systems online. I will speak only short status updates."
    typer.echo(f'Test phrase: "{phrase}"')
    if not (_have("piper") or _have("pyttsx3")):
        typer.echo(
            typer.style(
                "No local TTS engine installed — voice degrades to the silent Noop "
                "engine. Install with: pip install '.[voice]'",
                fg=typer.colors.YELLOW,
            )
        )
    else:
        typer.echo("A local TTS engine is available; audio playback is wired in Phase 7.")
    return 0
