"""Run the agent on a task (the bare `jarvis "<task>"` path)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from jarvis_code.agent import assemble_runtime
from jarvis_code.agent.loop import RunStatus
from jarvis_code.config import ConfigError, MissingAPIKeyError, load_config
from jarvis_code.permissions import PermissionMode, TerminalPrompter
from jarvis_code.sandbox import Sandbox, SandboxRootError, resolve_sandbox_root

from .options import GlobalOptions
from .ui import ConsoleReporter, print_finish_summary

_DANGEROUS_WARNING = (
    "⚠  DANGEROUS MODE: writes, edits, deletes, and shell commands run without "
    "per-action approval. Everything is still confined to the sandbox root."
)


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

    if opts.mode == PermissionMode.DANGEROUS:
        typer.echo(typer.style(_DANGEROUS_WARNING, fg=typer.colors.RED, bold=True))

    typer.echo(typer.style(f"jarvis [{opts.mode.value}] · sandbox: {root}", dim=True))
    typer.echo("")

    try:
        runtime = assemble_runtime(
            config=config,
            sandbox=sandbox,
            mode=opts.mode,
            provider=None,
            prompter=TerminalPrompter(),
            reporter=ConsoleReporter(),
        )
    except MissingAPIKeyError as exc:
        typer.echo(str(exc), err=True)
        return 1

    result = asyncio.run(runtime.run(task))
    print_finish_summary(result)
    return 0 if result.status == RunStatus.COMPLETE else 1
