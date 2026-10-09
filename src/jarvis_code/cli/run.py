"""Run the agent on a task (the bare `jarvis "<task>"` path)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from jarvis_code.agent import assemble_runtime, split_model_spec
from jarvis_code.agent.loop import RunStatus
from jarvis_code.agent.memory import save_project_memory
from jarvis_code.config import ConfigError, Effort, load_config
from jarvis_code.permissions import PermissionMode
from jarvis_code.sandbox import Sandbox, SandboxRootError, resolve_sandbox_root
from jarvis_code.voice import VoiceReporter, VoiceSubsystem, select_engine

from .approval import ConsolePrompter
from .interrupts import route_sigint
from .options import GlobalOptions
from .settings import DANGEROUS_WARNING
from .steer import make_console_asker
from .ui import CompositeReporter, ConsoleReporter, print_finish_summary

_MADMAX_HEADSUP = (
    "⚠  madmax effort — unlimited steps + sub-agents: slower, more tokens. "
    "Ctrl-C stops a turn."
)


def resolve_effort(opts: GlobalOptions, config) -> Effort:
    """CLI --effort overrides the config's [agent] effort."""
    return opts.effort or config.agent.effort


def resolve_model_flags(opts: GlobalOptions, config) -> tuple[str | None, str | None]:
    """``(provider, model)`` from --provider/--model.

    ``--model openai/gpt-5`` names both at once; an explicit --provider wins if
    the two disagree. The prefix only counts when it is a configured provider,
    so model ids that contain a slash are passed through untouched.
    """
    provider, model = opts.provider, opts.model
    if model:
        prefix, bare = split_model_spec(model, set(config.providers))
        if prefix is not None:
            provider, model = provider or prefix, bare
    return provider, model


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
        typer.echo(typer.style(DANGEROUS_WARNING, fg=typer.colors.RED, bold=True))
    if effort == Effort.MADMAX:
        typer.echo(typer.style(_MADMAX_HEADSUP, fg=typer.colors.YELLOW, bold=True))

    voice_on = config.voice.enabled if opts.voice is None else opts.voice

    def header(model: str) -> None:
        """One dim line saying what is about to run, and where."""
        from .chat import _short_path  # local: chat imports this module

        parts = ["jarvis", opts.mode.value, f"effort {effort.value}"]
        if model:
            parts.append(model)
        parts.append(_short_path(root))
        typer.echo(typer.style(" · ".join(parts), dim=True))

    async def _go():
        console = ConsoleReporter()
        reporter = console
        voice_sub: VoiceSubsystem | None = None
        if voice_on:
            voice_sub = VoiceSubsystem(select_engine(config.voice), config.voice)
            await voice_sub.start()
            reporter = CompositeReporter([console, VoiceReporter(voice_sub)])
        provider_name, model = resolve_model_flags(opts, config)
        try:
            runtime = assemble_runtime(
                config=config,
                sandbox=sandbox,
                mode=opts.mode,
                provider=None,
                provider_name=provider_name,  # --provider / --model overrides
                model=model,
                prompter=ConsolePrompter(before_prompt=console.quiet),
                reporter=reporter,
                effort=effort,
                asker=make_console_asker(),  # ask_user can clarify when stdin is a TTY
            )
            provider = runtime.provider
            header(" · ".join(p for p in (getattr(provider, "name", ""), getattr(provider, "model", "")) if p))
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
        typer.echo(typer.style("    notes saved to JARVIS.md (context for next time)", dim=True))
    if result.status == RunStatus.ABORTED:
        return 130  # conventional "interrupted" exit status
    return 0 if result.status == RunStatus.COMPLETE else 1
