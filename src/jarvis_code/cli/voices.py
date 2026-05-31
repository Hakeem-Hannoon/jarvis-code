"""`jarvis voices list` / `jarvis voices test` — backed by the real engines."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from jarvis_code.config import (
    ConfigError,
    VoiceConfig,
    VoiceEngineName,
    find_config_path,
    load_config,
)
from jarvis_code.voice import VoiceSubsystem, engine_available, select_engine

from .options import GlobalOptions

_TEST_PHRASE = "Voice systems online. I speak only short status updates."


def _load_voice_config(opts: GlobalOptions) -> VoiceConfig:
    base = Path(opts.sandbox) if opts.sandbox else Path.cwd()
    cfg_path = find_config_path(base)
    if cfg_path is None:
        return VoiceConfig()  # defaults: piper -> system -> noop fallback
    try:
        return load_config(cfg_path).voice
    except ConfigError:
        return VoiceConfig()


def voices_list(opts: GlobalOptions) -> int:
    voice = _load_voice_config(opts)
    typer.echo(typer.style("Voice engines", bold=True))
    for name in (VoiceEngineName.PIPER, VoiceEngineName.SYSTEM, VoiceEngineName.NOOP):
        available = engine_available(name, voice)
        mark = typer.style("available", fg=typer.colors.GREEN) if available else "not available"
        typer.echo(f"  {name.value:8} {mark}")
    typer.echo("")
    typer.echo(f"Configured engine:    {voice.engine.value}")
    typer.echo(f"Fallback engine:      {voice.fallback_engine.value}")
    typer.echo(f"Default voice preset:  {voice.default_voice}")
    typer.echo(f"Voice output enabled:  {voice.enabled}")
    selected = select_engine(voice)
    typer.echo(f"Would use:             {selected.name}")
    return 0


def voices_test(opts: GlobalOptions) -> int:
    voice = _load_voice_config(opts)
    engine = select_engine(voice)
    typer.echo(f'Test phrase: "{_TEST_PHRASE}"')

    async def _run() -> None:
        subsystem = VoiceSubsystem(engine, voice)
        await subsystem.start()
        subsystem.say(_TEST_PHRASE)
        await subsystem.drain()
        await subsystem.aclose()

    asyncio.run(_run())

    if engine.name == "noop":
        typer.echo(
            typer.style(
                "No audio engine available — spoke via the silent Noop engine. "
                "Install local TTS with: pip install '.[voice]'  (and set a Piper "
                "model_path in config).",
                fg=typer.colors.YELLOW,
            )
        )
    else:
        typer.echo(f"Spoke via the '{engine.name}' engine.")
    return 0
