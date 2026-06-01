"""Interactive chat session — bare ``jarvis`` with no task.

One runtime is built and reused across turns, so the conversation (context,
session transcript, run state) persists. Each line you type is a turn; the agent
works it to completion, then you're prompted again. Slash commands control the
session without leaving it.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import typer

from jarvis_code.agent import AgentRuntime, assemble_runtime
from jarvis_code.agent.loop import RunStatus
from jarvis_code.config import ConfigError, MissingAPIKeyError, load_config
from jarvis_code.input import MicController, build_mic_controller
from jarvis_code.models import ProviderError
from jarvis_code.permissions import PermissionMode, TerminalPrompter
from jarvis_code.sandbox import Sandbox, SandboxRootError, resolve_sandbox_root
from jarvis_code.voice import VoiceReporter, VoiceSubsystem, select_engine

from .lineedit import colored_prompt, save_history, setup_line_editing
from .options import GlobalOptions
from .ui import CompositeReporter, ConsoleReporter

_EXIT = {"/exit", "/quit", "exit", "quit", ":q"}
_TALK = {"/talk", "/ptt"}

_HELP = """\
Commands:
  /help                 show this help
  /talk (or /ptt)       push-to-talk: speak your next message, Enter to stop
  /mode plan|auto|dangerous   switch permission mode for the rest of the session
  /sandbox              show the current sandbox root
  /exit (or Ctrl-D)     leave the chat

Editing: ↑/↓ history · ←/→ move · Ctrl-A/E start/end · Ctrl-K/U kill · Ctrl-W del word
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

    mic = build_mic_controller(config.voice.input)
    voice_on = config.voice.enabled if opts.voice is None else opts.voice

    async def _go() -> int:
        reporter = ConsoleReporter()
        voice_sub: VoiceSubsystem | None = None
        engine_label = config.voice.engine.value
        if voice_on:
            engine = select_engine(config.voice)
            engine_label = engine.name
            voice_sub = VoiceSubsystem(engine, config.voice)
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

        setup_line_editing()
        _print_banner(opts.mode, root, mic, voice_on=voice_on, engine_label=engine_label)
        try:
            await chat_loop(runtime, prompt_fn=_read_line, echo=typer.echo, mic=mic)
        finally:
            save_history()
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


def _short_path(p: Path) -> str:
    s = str(p)
    home = str(Path.home())
    return "~" + s[len(home):] if s.startswith(home) else s


def _print_banner(
    mode: PermissionMode,
    root: Path,
    mic: MicController | None = None,
    *,
    voice_on: bool = False,
    engine_label: str = "off",
) -> None:
    c = typer.colors
    typer.echo(
        typer.style("  JARVIS  ", fg=c.BLACK, bg=c.CYAN, bold=True)
        + typer.style("  local coding agent", fg=c.CYAN, bold=True)
    )
    if mode == PermissionMode.DANGEROUS:
        typer.echo(typer.style("  " + _DANGEROUS_WARNING, fg=c.RED, bold=True))

    status = [f"mode {mode.value}", f"dir {_short_path(root)}",
              f"voice {engine_label}" if voice_on else "voice off"]
    if mic is not None and mic.enabled:
        status.append(mic.mic_bar())
    typer.echo(typer.style("  " + "   ·   ".join(status), dim=True))

    hints = ["/help", "↑↓ history", "/exit"]
    if mic is not None and mic.enabled:
        hints.insert(1, "/talk to speak")
    typer.echo(typer.style("  " + "   ·   ".join(hints), dim=True))
    typer.echo("")


async def _wait_for_enter() -> None:
    """Block (off the event loop) until the user presses Enter."""
    await asyncio.to_thread(input, "")


async def _capture_voice(
    mic: MicController | None,
    echo: Callable[[str], None],
    wait_for_stop: Callable[[], Awaitable[None]],
) -> str | None:
    """Run one push-to-talk capture and return the transcript (or None).

    Degrades with a clear, actionable message whenever voice input is off,
    Muted, or the mic/STT backend is unavailable — never raises.
    """
    if mic is None:
        echo(typer.style("Voice input isn't configured.", fg=typer.colors.YELLOW))
        return None
    reason = await mic.availability_reason()
    if reason is not None:
        echo(typer.style(reason, fg=typer.colors.YELLOW))
        return None

    echo(typer.style("🎙  Recording — press Enter to stop.", fg=typer.colors.MAGENTA))
    stop = threading.Event()
    task = asyncio.create_task(mic.record_ptt(stop=stop))
    try:
        await wait_for_stop()
    finally:
        stop.set()
    text = ((await task) or "").strip()
    if not text:
        echo(typer.style("(heard nothing)", dim=True))
        return None
    echo(typer.style(f"you 🎙 › {text}", fg=typer.colors.CYAN, bold=True))
    return text


async def chat_loop(
    runtime: AgentRuntime,
    *,
    prompt_fn: Callable[[str], str],
    echo: Callable[[str], None],
    mic: MicController | None = None,
    stop_waiter: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """The testable core loop. ``prompt_fn`` returns a line or raises EOFError."""
    waiter = stop_waiter or _wait_for_enter
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
        if message in _TALK:
            spoken = await _capture_voice(mic, echo, waiter)
            if spoken is None:
                continue
            message = spoken  # fall through and run the transcribed message

        try:
            result = await runtime.run(message)
        except ProviderError as exc:
            echo(typer.style(str(exc), fg=typer.colors.RED))
            continue
        _print_turn_footer(result, echo)


def _you_prompt() -> str:
    return colored_prompt("you ❯ ")


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
