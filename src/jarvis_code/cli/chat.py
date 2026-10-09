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

from jarvis_code import __version__
from jarvis_code.agent import AgentRuntime, assemble_runtime
from jarvis_code.agent.memory import save_project_memory
from jarvis_code.config import ConfigError, Effort, load_config
from jarvis_code.events import EventBus, InstructionQueue
from jarvis_code.input import MicController, build_mic_controller
from jarvis_code.models import ProviderError
from jarvis_code.permissions import PermissionMode
from jarvis_code.sandbox import Sandbox, SandboxRootError, resolve_sandbox_root
from jarvis_code.utils.threads import run_in_daemon_thread
from jarvis_code.voice import VoiceReporter, VoiceSubsystem, select_engine

from .approval import ConsolePrompter
from .interrupts import restore_terminal_state, route_sigint, save_terminal_state
from .keyinput import IdleAction, IdleIntent, read_idle_intent, supports_raw_keys
from .lineedit import colored_prompt, save_history, setup_line_editing
from .options import GlobalOptions
from .run import _DANGEROUS_WARNING, _MADMAX_HEADSUP, resolve_effort
from .steer import (
    PausingQueuePrompter,
    SteerSession,
    looks_like_command,
    make_console_asker,
)
from .ui import CompositeReporter, ConsoleReporter, format_tokens, print_turn_footer

_EXIT = {"/exit", "/quit", "exit", "quit", ":q"}
_TALK = {"/talk", "/ptt"}
_COMPRESS = {"/compress", "/compact"}

# A dim line of key commands, shown once under the banner.
_PROMPT_HINTS = "/help · /status · /compress · /mode · /talk · /exit"

_COMMANDS = [
    ("/help", "show this help"),
    ("/status", "mode, model, sandbox and how full the context is"),
    ("/mode plan|auto|dangerous", "switch permission mode for the rest of the session"),
    ("/compress (/compact)", "compact the conversation now to free up context space"),
    ("/clear", "forget the conversation so far and start fresh"),
    ("/sandbox", "show the current sandbox root"),
    ("/talk (/ptt)", "push-to-talk: speak your next message, Enter to stop"),
    ("/mic", "cycle mic mode (Open Mic / Push to Talk / Muted)"),
    ("/exit (Ctrl-D)", "leave the chat"),
]
_WHILE_WORKING = [
    ("Ctrl-C", "stop the current turn (the chat stays open)"),
    ("type + Enter", "steer it — folded in after the current small step"),
    ("", '("wait…"/"actually…" to adjust, "stop" to halt)'),
]
_HELP_NOTES = """\
Voice: when voice input is on, just tap Space at the prompt to start talking
(terminals can't sense key-release, so it's tap-to-start; Enter stops recording).

Editing: ↑/↓ history · ←/→ move · Ctrl-A/E start/end · Ctrl-K/U kill · Ctrl-W del word
Anything else is sent to the agent as your next message."""


def _help_text() -> str:
    """The /help screen: aligned columns, command names highlighted."""
    width = max(len(name) for name, _ in _COMMANDS + _WHILE_WORKING) + 2

    def rows(items: list[tuple[str, str]]) -> list[str]:
        return [
            "  " + typer.style(f"{name:<{width}}", fg=typer.colors.CYAN) + typer.style(text, dim=not name)
            for name, text in items
        ]

    return "\n".join(
        [
            typer.style("Commands:", bold=True),
            *rows(_COMMANDS),
            "",
            typer.style("While the agent is working:", bold=True),
            *rows(_WHILE_WORKING),
            "",
            typer.style(_HELP_NOTES, dim=True),
        ]
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
        console = ConsoleReporter()
        reporter = console
        voice_sub: VoiceSubsystem | None = None
        engine_label = config.voice.engine.value
        if voice_on:
            engine = select_engine(config.voice)
            engine_label = engine.name
            voice_sub = VoiceSubsystem(engine, config.voice)
            await voice_sub.start()
            reporter = CompositeReporter([console, VoiceReporter(voice_sub)])
        # Live steering: one shared instruction queue feeds both the steering
        # reader (talk while it works) and the approval prompt (answers route
        # here too). The prompter pauses the reader while it owns the terminal.
        bus = EventBus()
        instructions = InstructionQueue(bus)
        # Steering notes print through the console reporter so they never
        # collide with its spinner.
        steer = SteerSession(instructions, echo=console.say)
        prompter = PausingQueuePrompter(
            instructions,
            ConsolePrompter(before_prompt=console.quiet),
            steer.prompt_active,
            echo=console.say,
        )
        # ask_user reads a line from the terminal, pausing the steering reader.
        asker = make_console_asker(steer.prompt_active)
        try:
            runtime = assemble_runtime(
                config=config,
                sandbox=sandbox,
                mode=opts.mode,
                provider=None,
                provider_name=opts.provider,  # --provider / --model overrides
                model=opts.model,
                prompter=prompter,
                reporter=reporter,
                effort=effort,
                instructions=instructions,
                bus=bus,
                asker=asker,
            )
        except ConfigError as exc:  # missing key, unknown --provider, bad endpoint…
            typer.echo(str(exc), err=True)
            if voice_sub is not None:
                await voice_sub.aclose()
            return 1

        setup_line_editing()
        _print_banner(
            opts.mode, root, mic, voice_on=voice_on, engine_label=engine_label,
            effort=effort, model=_model_label(runtime),
        )
        if effort == Effort.MADMAX:
            typer.echo(typer.style("  " + _MADMAX_HEADSUP, fg=typer.colors.YELLOW, bold=True))
        # Tap-Space-to-talk only when the mic is on and we have a real TTY;
        # otherwise the chat stays a plain typed-line prompt.
        key_reader = None
        if mic.enabled and supports_raw_keys():
            def key_reader(edit_prompt: str) -> IdleIntent:  # noqa: E731
                return read_idle_intent(_plain_voice_prompt(), edit_prompt)
        idle = IdleRead()
        try:
            # Ctrl-C stops the running turn; at the idle prompt it leaves the chat.
            with route_sigint(runtime, on_idle=idle.cancel):
                await chat_loop(
                    runtime, prompt_fn=_read_line, echo=typer.echo, mic=mic,
                    key_reader=key_reader, steer=steer, idle=idle,
                )
            # Persist a concise notes block so the next session has context —
            # except in plan mode, which is read-only and must not write files.
            if (
                runtime.executor.permissions.mode != PermissionMode.PLAN
                and save_project_memory(sandbox, runtime.state) is not None
            ):
                typer.echo(typer.style("    notes saved to JARVIS.md for next time", dim=True))
        finally:
            save_history()
            if voice_sub is not None:
                await voice_sub.aclose()
        return 0

    terminal = save_terminal_state()
    try:
        return asyncio.run(_go())
    finally:
        restore_terminal_state(terminal)


class IdleRead:
    """The in-flight idle prompt read, so Ctrl-C at the prompt can end the chat.

    The read blocks in a daemon thread (``input()`` cannot be cancelled);
    cancelling the future just stops *waiting* for it, which lets the chat say
    goodbye and exit instead of hanging until the user presses Enter.
    """

    def __init__(self) -> None:
        self.future: asyncio.Future | None = None

    def cancel(self) -> None:
        if self.future is not None and not self.future.done():
            self.future.cancel()


async def _idle_read(fn: Callable[[str], object], prompt: str, idle: IdleRead | None):
    future = run_in_daemon_thread(fn, prompt)
    if idle is not None:
        idle.future = future
    try:
        return await future
    finally:
        if idle is not None:
            idle.future = None


def _read_line(prompt: str) -> str:
    """Blocking input; raises EOFError on Ctrl-D / KeyboardInterrupt to exit."""
    try:
        return input(prompt)
    except KeyboardInterrupt:  # treat Ctrl-C at the prompt as "leave"
        raise EOFError from None


def _short_path(p: Path) -> str:
    """``p`` with the home directory abbreviated to ``~`` (whole components only)."""
    try:
        rel = p.relative_to(Path.home())
    except ValueError:  # not under home (e.g. /home/alice2 vs /home/alice)
        return str(p)
    return "~" if str(rel) == "." else f"~/{rel.as_posix()}"


def _model_label(runtime: object) -> str:
    """'anthropic · claude-x' for the banner / status (never anything secret)."""
    provider = getattr(runtime, "provider", None)
    name = getattr(provider, "name", "") or ""
    model = getattr(provider, "model", "") or ""
    return " · ".join(part for part in (name, model) if part)


_MODE_COLOR = {
    PermissionMode.PLAN: typer.colors.GREEN,
    PermissionMode.AUTO: typer.colors.CYAN,
    PermissionMode.DANGEROUS: typer.colors.RED,
}


def _print_banner(
    mode: PermissionMode,
    root: Path,
    mic: MicController | None = None,
    *,
    voice_on: bool = False,
    engine_label: str = "off",
    effort: Effort = Effort.MEDIUM,
    model: str = "",
) -> None:
    c = typer.colors
    typer.echo("")
    typer.echo(
        typer.style("  JARVIS  ", fg=c.BLACK, bg=c.CYAN, bold=True)
        + typer.style("  local coding agent", fg=c.CYAN, bold=True)
        + typer.style(f"  v{__version__}", dim=True)
    )
    typer.echo("")
    if mode == PermissionMode.DANGEROUS:
        typer.echo(typer.style("  " + _DANGEROUS_WARNING, fg=c.RED, bold=True))

    # Mode + effort as prominent coloured chips so the user always knows the
    # posture the agent is running under (mode colour ranks plan→auto→dangerous).
    chips = (
        "  "
        + typer.style(f" MODE: {mode.value} ", fg=c.BLACK, bg=_MODE_COLOR[mode], bold=True)
        + "  "
        + typer.style(f" EFFORT: {effort.value} ", fg=c.BLACK, bg=c.WHITE, bold=True)
    )
    if model:
        chips += "  " + typer.style(model, fg=c.CYAN)
    typer.echo(chips)

    meta = [f"dir {_short_path(root)}", f"voice {engine_label}" if voice_on else "voice off"]
    if mic is not None and mic.enabled:
        meta.append(mic.mic_bar())
    typer.echo(typer.style("  " + "   ·   ".join(meta), dim=True))

    if mic is not None and mic.enabled:
        typer.echo(
            typer.style("  🎙  Press Space to talk", fg=c.MAGENTA, bold=True)
            + typer.style("  ·  Enter stops recording  ·  or type /talk", dim=True)
        )
    typer.echo(
        typer.style(
            "  type while it works to steer   ·   Ctrl-C stops a turn   ·   ↑↓ history",
            dim=True,
        )
    )


async def _wait_for_enter() -> None:
    """Block (off the event loop) until the user presses Enter."""
    await run_in_daemon_thread(input, "")


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
    idle: IdleRead | None = None,
) -> None:
    """The testable core loop.

    Without ``key_reader`` each idle turn is one ``prompt_fn`` line (the original
    behavior, used by tests and non-TTY runs). With ``key_reader`` an idle turn is
    a single keypress: Space starts a voice capture; any typed character begins a
    normal edited line. ``steer``, when given, reads live steering while a turn
    runs. Either way ``EOFError`` leaves the chat.
    """
    waiter = stop_waiter or _wait_for_enter
    _print_command_hints(echo)  # once, under the banner — not before every prompt
    while True:
        echo("")
        try:
            if key_reader is not None:
                intent = await _idle_read(key_reader, _you_prompt(), idle)
            else:
                intent = IdleIntent(
                    IdleAction.LINE, await _idle_read(prompt_fn, _you_prompt(), idle)
                )
        except (EOFError, asyncio.CancelledError):  # Ctrl-D, or Ctrl-C at the prompt
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
            echo(_help_text())
            continue
        if message == "/sandbox":
            echo(str(runtime.executor.ctx.sandbox.root))
            continue
        if message == "/status":
            _handle_status(runtime, echo)
            continue
        if message == "/clear":
            _handle_clear(runtime, echo)
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
        elif looks_like_command(message):
            # A typo'd command ("/hlep") used to be sent to the model as a task.
            echo(typer.style(f"  unknown command {message.split()[0]} — try /help", fg=typer.colors.YELLOW))
            continue

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


def _handle_status(runtime: AgentRuntime, echo: Callable[[str], None]) -> None:
    """Show the session's posture and how full the context is (/status)."""
    ctx = runtime.context
    used, budget = ctx.estimated_chars(), ctx.max_chars
    pct = min(100, round(100 * used / budget)) if budget else 0
    filled = round(pct / 5)
    bar = "█" * filled + "░" * (20 - filled)
    color = typer.colors.GREEN if pct < 70 else typer.colors.YELLOW if pct < 90 else typer.colors.RED
    rows = [
        ("mode", runtime.executor.permissions.mode.value),
        ("model", _model_label(runtime) or "(unknown)"),
        ("sandbox", str(runtime.executor.ctx.sandbox.root)),
        ("messages", str(len(ctx.messages))),
    ]
    for label, value in rows:
        echo(typer.style(f"  {label:<10}", dim=True) + value)
    echo(
        typer.style(f"  {'context':<10}", dim=True)
        + typer.style(bar, fg=color)
        + typer.style(f" {pct}%  (~{format_tokens(used)} of {format_tokens(budget)} chars)", dim=True)
    )
    if runtime.state.files_changed:
        changed = ", ".join(runtime.state.files_changed[-8:])
        echo(typer.style(f"  {'changed':<10}", dim=True) + changed)


def _handle_clear(runtime: AgentRuntime, echo: Callable[[str], None]) -> None:
    """Forget the conversation so far (/clear). Files on disk are untouched."""
    dropped = len(runtime.context.messages)
    runtime.context.messages.clear()
    echo(typer.style(f"  conversation cleared ({dropped} messages forgotten).", fg=typer.colors.GREEN))


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
        echo(typer.style("  " + _DANGEROUS_WARNING, fg=typer.colors.RED, bold=True))
    # Tell the model about the change so its self-description stays accurate.
    runtime.context.add_user(f"[mode changed to {new_mode.value}]")
    echo("  mode → " + typer.style(f" {new_mode.value} ", fg=typer.colors.BLACK, bg=_MODE_COLOR[new_mode], bold=True))


def _print_turn_footer(result, echo: Callable[[str], None]) -> None:
    """Close a turn: outcome (with the reason if it failed), stats, files changed."""
    print_turn_footer(result, echo)
