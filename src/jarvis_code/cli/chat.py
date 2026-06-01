"""Interactive chat session — bare ``jarvis`` with no task.

One runtime is built and reused across turns, so the conversation (context,
session transcript, run state) persists. Each line you type is a turn; the agent
works it to completion, then you're prompted again. Slash commands control the
session without leaving it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import typer

from jarvis_code.agent import AgentRuntime, assemble_runtime
from jarvis_code.agent.loop import RunStatus
from jarvis_code.config import ConfigError, MissingAPIKeyError, load_config
from jarvis_code.models import ProviderError
from jarvis_code.permissions import PermissionMode, TerminalPrompter
from jarvis_code.sandbox import Sandbox, SandboxRootError, resolve_sandbox_root
from jarvis_code.voice import VoiceReporter, VoiceSubsystem, select_engine

from .options import GlobalOptions
from .ui import CompositeReporter, ConsoleReporter

_EXIT = {"/exit", "/quit", "exit", "quit", ":q"}

_HELP = """\
Commands:
  /help                 show this help
  /mode plan|auto|dangerous   switch permission mode for the rest of the session
  /sandbox              show the current sandbox root
  /exit (or Ctrl-D)     leave the chat
Anything else is sent to the agent as your next message."""

_DANGEROUS_WARNING = (
    "⚠  DANGEROUS MODE: writes/edits/deletes/shell run without per-action "
    "approval. Still confined to the sandbox root."
)


def run_chat(opts: GlobalOptions) -> int:
    """Resolve the sandbox/config, build one runtime, and run the chat loop."""
    try:
        root = resolve_sandbox_root(Path.cwd(), opts.sandbox)
    except SandboxRootError as exc:
        typer.echo(str(exc), err=True)
        return 2
    sandbox = Sandbox(root)

    try:
        config = load_config(base_dir=root)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        return 1

    async def _go() -> int:
        reporter = ConsoleReporter()
        voice_sub: VoiceSubsystem | None = None
        if config.voice.enabled:
            voice_sub = VoiceSubsystem(select_engine(config.voice), config.voice)
            await voice_sub.start()
            reporter = CompositeReporter([ConsoleReporter(), VoiceReporter(voice_sub)])
        try:
            runtime = assemble_runtime(
                config=config,
                sandbox=sandbox,
                mode=opts.mode,
                provider=None,
                prompter=TerminalPrompter(),
                reporter=reporter,
            )
        except MissingAPIKeyError as exc:
            typer.echo(str(exc), err=True)
            return 1

        _print_banner(opts.mode, root)
        try:
            await chat_loop(runtime, prompt_fn=_read_line, echo=typer.echo)
        finally:
            if voice_sub is not None:
                await voice_sub.aclose()
        return 0

    return asyncio.run(_go())


def _read_line(prompt: str) -> str:
    """Blocking input; raises EOFError on Ctrl-D / KeyboardInterrupt to exit."""
    try:
        return input(prompt)
    except KeyboardInterrupt:  # treat Ctrl-C at the prompt as "leave"
        raise EOFError from None


def _print_banner(mode: PermissionMode, root: Path) -> None:
    if mode == PermissionMode.DANGEROUS:
        typer.echo(typer.style(_DANGEROUS_WARNING, fg=typer.colors.RED, bold=True))
    typer.echo(typer.style(f"jarvis [{mode.value}] · sandbox: {root}", dim=True))
    typer.echo("Live chat. Type a message; /help for commands, /exit to quit.")
    typer.echo("")


async def chat_loop(
    runtime: AgentRuntime,
    *,
    prompt_fn: Callable[[str], str],
    echo: Callable[[str], None],
) -> None:
    """The testable core loop. ``prompt_fn`` returns a line or raises EOFError."""
    while True:
        try:
            line = await asyncio.to_thread(prompt_fn, _you_prompt())
        except EOFError:
            echo("")
            echo("Bye.")
            return

        message = line.strip()
        if not message:
            continue
        if message in _EXIT:
            echo("Bye.")
            return
        if message == "/help":
            echo(_HELP)
            continue
        if message == "/sandbox":
            echo(str(runtime.executor.ctx.sandbox.root))
            continue
        if message.startswith("/mode"):
            _handle_mode(runtime, message, echo)
            continue

        try:
            result = await runtime.run(message)
        except ProviderError as exc:
            echo(typer.style(str(exc), fg=typer.colors.RED))
            continue
        _print_turn_footer(result, echo)


def _you_prompt() -> str:
    return typer.style("you › ", fg=typer.colors.CYAN, bold=True)


def _handle_mode(runtime: AgentRuntime, message: str, echo: Callable[[str], None]) -> None:
    parts = message.split()
    valid = {m.value for m in PermissionMode}
    if len(parts) != 2 or parts[1] not in valid:
        echo("usage: /mode plan|auto|dangerous")
        return
    new_mode = PermissionMode(parts[1])
    runtime.executor.permissions.mode = new_mode
    if new_mode == PermissionMode.DANGEROUS:
        echo(typer.style(_DANGEROUS_WARNING, fg=typer.colors.RED, bold=True))
    # Tell the model about the change so its self-description stays accurate.
    runtime.context.add_user(f"[mode changed to {new_mode.value}]")
    echo(f"mode → {new_mode.value}")


def _print_turn_footer(result, echo: Callable[[str], None]) -> None:
    state = result.state
    if result.status != RunStatus.COMPLETE:
        echo(typer.style(f"[{result.status.value}]", fg=typer.colors.YELLOW))
    touched = ", ".join(state.files_changed[-6:])
    if touched:
        echo(typer.style(f"  files: {touched}", dim=True))
