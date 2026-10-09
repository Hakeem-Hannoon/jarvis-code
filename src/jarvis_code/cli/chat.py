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
from jarvis_code.agent.memory import save_project_memory
from jarvis_code.config import ConfigError, Effort, MissingAPIKeyError, load_config
from jarvis_code.events import EventBus, InstructionQueue
from jarvis_code.input import MicController, build_mic_controller
from jarvis_code.models import ProviderError
from jarvis_code.permissions import PermissionMode, TerminalPrompter
from jarvis_code.sandbox import Sandbox, SandboxRootError, resolve_sandbox_root
from jarvis_code.voice import VoiceReporter, VoiceSubsystem, select_engine

from .keyinput import IdleAction, IdleIntent, read_idle_intent, supports_raw_keys
from .lineedit import colored_prompt, save_history, setup_line_editing
from .options import GlobalOptions
from .run import _MADMAX_HEADSUP, resolve_effort
from .steer import PausingQueuePrompter, SteerSession, make_console_asker
from .ui import CompositeReporter, ConsoleReporter

_EXIT = {"/exit", "/quit", "exit", "quit", ":q"}
_TALK = {"/talk", "/ptt"}
_COMPRESS = {"/compress", "/compact"}

# A dim line of key commands shown at the prompt each turn.
_PROMPT_HINTS = "/help · /compress · /mode · /talk · /exit"

_HELP = """\
Commands:
  /help                 show this help
  /compress (/compact)  compact the conversation now to free up context space
  /talk (or /ptt)       push-to-talk: speak your next message, Enter to stop
  /mic                  cycle mic mode (Open Mic / Push to Talk / Muted)
  /mode plan|auto|dangerous   switch permission mode for the rest of the session
  /sandbox              show the current sandbox root
  /exit (or Ctrl-D)     leave the chat

Voice: when voice input is on, just tap Space at the prompt to start talking
(terminals can't sense key-release, so it's tap-to-start; Enter stops recording).

Steering: while the agent is working, type a line and press Enter to steer it —
it's folded in after the current small step. Say "wait"/"actually …" to adjust,
"stop" to halt, or just answer an approval prompt (yes/no/your own words).

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
    effort = resolve_effort(opts, config)

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
        # Live steering: one shared instruction queue feeds both the steering
        # reader (talk while it works) and the approval prompt (answers route
        # here too). The prompter pauses the reader while it owns the terminal.
        bus = EventBus()
        instructions = InstructionQueue(bus)
        steer = SteerSession(instructions, echo=typer.echo)
        prompter = PausingQueuePrompter(
            instructions, TerminalPrompter(), steer.prompt_active, echo=typer.echo
        )
        # ask_user reads a line from the terminal, pausing the steering reader.
        asker = make_console_asker(steer.prompt_active)
        try:
            runtime = assemble_runtime(
                config=config,
                sandbox=sandbox,
                mode=opts.mode,
                provider=None,
                prompter=prompter,
                reporter=reporter,
                effort=effort,
                instructions=instructions,
                bus=bus,
                asker=asker,
            )
        except MissingAPIKeyError as exc:
            typer.echo(str(exc), err=True)
            return 1

        setup_line_editing()
        if effort == Effort.MADMAX:
            typer.echo(typer.style(_MADMAX_HEADSUP, fg=typer.colors.YELLOW, bold=True))
        _print_banner(opts.mode, root, mic, voice_on=voice_on, engine_label=engine_label, effort=effort)
        # Tap-Space-to-talk only when the mic is on and we have a real TTY;
        # otherwise the chat stays a plain typed-line prompt.
        key_reader = None
        if mic.enabled and supports_raw_keys():
            def key_reader(edit_prompt: str) -> IdleIntent:  # noqa: E731
                return read_idle_intent(_plain_voice_prompt(), edit_prompt)
        try:
            await chat_loop(
                runtime, prompt_fn=_read_line, echo=typer.echo, mic=mic,
                key_reader=key_reader, steer=steer,
            )
            # Persist a concise notes block so the next session has context.
            if save_project_memory(sandbox, runtime.state) is not None:
                typer.echo(typer.style("  notes saved to JARVIS.md for next time", dim=True))
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
    effort: Effort = Effort.MEDIUM,
) -> None:
    c = typer.colors
    typer.echo(
        typer.style("  JARVIS  ", fg=c.BLACK, bg=c.CYAN, bold=True)
        + typer.style("  local coding agent", fg=c.CYAN, bold=True)
    )
    if mode == PermissionMode.DANGEROUS:
        typer.echo(typer.style("  " + _DANGEROUS_WARNING, fg=c.RED, bold=True))

    # Mode + effort as prominent coloured chips so the user always knows the
    # posture the agent is running under (mode colour ranks plan→auto→dangerous).
    mode_bg = {
        PermissionMode.PLAN: c.GREEN,
        PermissionMode.AUTO: c.CYAN,
        PermissionMode.DANGEROUS: c.RED,
    }[mode]
    typer.echo(
        "  "
        + typer.style(f" MODE: {mode.value} ", fg=c.BLACK, bg=mode_bg, bold=True)
        + "  "
        + typer.style(f" EFFORT: {effort.value} ", fg=c.BLACK, bg=c.WHITE, bold=True)
    )

    meta = [f"dir {_short_path(root)}", f"voice {engine_label}" if voice_on else "voice off"]
    if mic is not None and mic.enabled:
        meta.append(mic.mic_bar())
    typer.echo(typer.style("  " + "   ·   ".join(meta), dim=True))

    if mic is not None and mic.enabled:
        typer.echo(
            typer.style("  🎙  Press Space to talk", fg=c.MAGENTA, bold=True)
            + typer.style("  ·  Enter stops recording  ·  or type /talk", dim=True)
        )
    typer.echo(typer.style("  /help   ·   ↑↓ history   ·   type while it works to steer   ·   /exit", dim=True))
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
    key_reader: Callable[[str], IdleIntent] | None = None,
    steer: SteerSession | None = None,
) -> None:
    """The testable core loop.

    Without ``key_reader`` each idle turn is one ``prompt_fn`` line (the original
    behavior, used by tests and non-TTY runs). With ``key_reader`` an idle turn is
    a single keypress: Space starts a voice capture; any typed character begins a
    normal edited line. ``steer``, when given, reads live steering while a turn
    runs. Either way ``EOFError`` leaves the chat.
    """
    waiter = stop_waiter or _wait_for_enter
    while True:
        _print_command_hints(echo)
        try:
            if key_reader is not None:
                intent = await asyncio.to_thread(key_reader, _you_prompt())
            else:
                intent = IdleIntent(IdleAction.LINE, await asyncio.to_thread(prompt_fn, _you_prompt()))
        except EOFError:
            echo("")
            echo("Bye.")
            return

        if intent.action is IdleAction.NONE:
            continue
        if intent.action is IdleAction.TALK:
            spoken = await _capture_voice(mic, echo, waiter)
            if spoken is not None:
                await _run_message(runtime, spoken, echo, steer)
            continue

        message = intent.text.strip()
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
        if message == "/mic":
            _handle_mic(mic, echo)
            continue
        if message in _COMPRESS:
            _handle_compress(runtime, echo)
            continue
        if message.startswith("/mode"):
            _handle_mode(runtime, message, echo)
            continue
        if message in _TALK:
            spoken = await _capture_voice(mic, echo, waiter)
            if spoken is None:
                continue
            message = spoken  # fall through and run the transcribed message

        await _run_message(runtime, message, echo, steer)


async def _run_message(
    runtime: AgentRuntime,
    message: str,
    echo: Callable[[str], None],
    steer: SteerSession | None = None,
) -> None:
    """Run one agent turn for ``message`` and print its footer (errors are caught).

    ``steer`` (if given) reads live steering for the duration of the turn, so you
    can talk to the agent while it works; it stops as soon as the turn ends.
    """
    if steer is not None:
        steer.start()
    try:
        result = await runtime.run(message)
    except ProviderError as exc:
        echo(typer.style(str(exc), fg=typer.colors.RED))
        return
    finally:
        if steer is not None:
            steer.stop()
    _print_turn_footer(result, echo)


def _handle_mic(mic: MicController | None, echo: Callable[[str], None]) -> None:
    """Cycle the mic mode (Open Mic → Push to Talk → Muted)."""
    if mic is None or not getattr(mic, "enabled", False):
        echo(typer.style("Voice input isn't enabled (see /help).", fg=typer.colors.YELLOW))
        return
    mic.cycle_mode()
    echo(typer.style(mic.mic_bar(), dim=True))


def _handle_compress(runtime: AgentRuntime, echo: Callable[[str], None]) -> None:
    """Manually compact the conversation context to free up space (/compress)."""
    before = runtime.context.estimated_chars()
    if runtime.context.compact(runtime.state, force=True):
        after = runtime.context.estimated_chars()
        echo(typer.style(f"  context compressed: ~{before:,} → ~{after:,} chars", fg=typer.colors.GREEN))
    else:
        echo(typer.style("  nothing to compress yet — the conversation is still small.", dim=True))


def _print_command_hints(echo: Callable[[str], None]) -> None:
    """A dim, one-line reminder of key commands, shown at the prompt each turn."""
    echo(typer.style("  " + _PROMPT_HINTS, dim=True))


def _you_prompt() -> str:
    return colored_prompt("you ❯ ")


def _plain_voice_prompt() -> str:
    """Idle prompt shown for the raw single-key read (no readline width markers)."""
    return typer.style("you 🎙 ❯ ", fg=typer.colors.CYAN, bold=True)


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
