"""Run the agent on a task (the bare `jarvis "<task>"` path)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from jarvis_code.agent import assemble_runtime
from jarvis_code.agent.loop import RunStatus
from jarvis_code.agent.memory import save_project_memory
from jarvis_code.config import ConfigError, Effort, load_config
from jarvis_code.permissions import PermissionMode, TerminalPrompter
from jarvis_code.sandbox import Sandbox, SandboxRootError, resolve_sandbox_root
from jarvis_code.voice import VoiceReporter, VoiceSubsystem, select_engine

from .interrupts import route_sigint
from .options import GlobalOptions
from .steer import make_console_asker
from .ui import CompositeReporter, ConsoleReporter, print_finish_summary

_DANGEROUS_WARNING = (
    "⚠  DANGEROUS MODE: writes, edits, deletes, and shell commands run without "
    "per-action approval. Everything is still confined to the sandbox root."
)

_MADMAX_HEADSUP = (
    "⚠  madmax effort: unlimited steps, sub-agents enabled, larger responses — "
    "slower and more token-hungry (bigger bills). Ctrl-C to stop anytime."
)


def resolve_effort(opts: GlobalOptions, config) -> Effort:
    """CLI --effort overrides the config's [agent] effort."""
    return opts.effort or config.agent.effort


def run_task(opts: GlobalOptions, task: str) -> int:
    try:
        root = resolve_sandbox_root(Path.cwd(), opts.sandbox)
    except SandboxRootError as exc:
        typer.echo(str(exc), err=True)
        return 2
    sandbox = Sandbox(root)

    try:
        config = load_config(base_dir=root)
    except ConfigError as exc:
        # Friendly messages (ConfigNotFoundError / validation) print as-is.
        typer.echo(str(exc), err=True)
        return 1

    effort = resolve_effort(opts, config)

    if opts.mode == PermissionMode.DANGEROUS:
        typer.echo(typer.style(_DANGEROUS_WARNING, fg=typer.colors.RED, bold=True))
    if effort == Effort.MADMAX:
        typer.echo(typer.style(_MADMAX_HEADSUP, fg=typer.colors.YELLOW, bold=True))

    typer.echo(
        typer.style(f"jarvis [{opts.mode.value}] · effort {effort.value} · sandbox: {root}", dim=True)
    )
    typer.echo("")

    voice_on = config.voice.enabled if opts.voice is None else opts.voice

    async def _go():
        reporter = ConsoleReporter()
        voice_sub: VoiceSubsystem | None = None
        if voice_on:
            voice_sub = VoiceSubsystem(select_engine(config.voice), config.voice)
            await voice_sub.start()
            reporter = CompositeReporter([ConsoleReporter(), VoiceReporter(voice_sub)])
        try:
            runtime = assemble_runtime(
                config=config,
                sandbox=sandbox,
                mode=opts.mode,
                provider=None,
                provider_name=opts.provider,  # --provider / --model overrides
                model=opts.model,
                prompter=TerminalPrompter(),
                reporter=reporter,
                effort=effort,
                asker=make_console_asker(),  # ask_user can clarify when stdin is a TTY
            )
            # Ctrl-C stops the run cleanly (summary still printed) instead of
            # hanging on the in-flight request or dying with a traceback.
            with route_sigint(runtime):
                return await runtime.run(task)
        finally:
            if voice_sub is not None:
                await voice_sub.aclose()

    try:
        result = asyncio.run(_go())
    except ConfigError as exc:  # missing key, unknown --provider, bad endpoint…
        typer.echo(str(exc), err=True)
        return 1

    print_finish_summary(result)
    # plan mode is read-only: it must not write JARVIS.md either.
    if opts.mode != PermissionMode.PLAN and save_project_memory(sandbox, result.state) is not None:
        typer.echo(typer.style("  notes saved to JARVIS.md (context for next time)", dim=True))
    if result.status == RunStatus.ABORTED:
        return 130  # conventional "interrupted" exit status
    return 0 if result.status == RunStatus.COMPLETE else 1
