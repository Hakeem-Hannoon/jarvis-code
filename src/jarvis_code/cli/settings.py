"""In-chat switching: ``/model``, ``/provider``, ``/effort``, ``/mode``, ``/save``.

Each command works two ways:

* **with a value** — ``/model gpt-5``, ``/model openai/gpt-5``, ``/effort high``
  — it switches immediately;
* **bare** — ``/model`` — it opens a picker (arrow keys, type to filter).

A switch lasts for the session. ``/save`` writes the current provider, model and
effort into the config as the new default. All the real work is done by
``runtime.controls`` (see ``agent/controls.py``); this module is presentation.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

from jarvis_code.config import ConfigError, Effort, env_var_for, find_config_path
from jarvis_code.permissions import PermissionMode

from .configedit import save_defaults
from .picker import Option, pick

Echo = Callable[[str], None]
Chooser = Callable[..., "str | None"]

MODE_NOTES = {
    PermissionMode.PLAN: "read-only — look, search, plan; no changes",
    PermissionMode.AUTO: "asks before every write and shell command",
    PermissionMode.DANGEROUS: "no per-action approval (still sandboxed)",
}
EFFORT_NOTES = {
    Effort.LOW: "quick and minimal, capped at 15 steps",
    Effort.MEDIUM: "balanced",
    Effort.HIGH: "thorough — reasons about edge cases, verifies",
    Effort.MADMAX: "maximum rigor + sub-agents (slower, more tokens)",
}
MODE_COLOR = {
    PermissionMode.PLAN: typer.colors.GREEN,
    PermissionMode.AUTO: typer.colors.CYAN,
    PermissionMode.DANGEROUS: typer.colors.RED,
}
DANGEROUS_WARNING = "⚠  DANGEROUS MODE — no per-action approval. Still confined to the sandbox."
_SESSION_NOTE = "  (this session · /save to make it the default)"


def _dim(text: str) -> str:
    return typer.style(text, dim=True)


def _warn(echo: Echo, text: str) -> None:
    for line in text.splitlines():
        echo(typer.style(f"  {line}", fg=typer.colors.YELLOW))


def _controls(runtime: Any, echo: Echo) -> Any | None:
    controls = getattr(runtime, "controls", None)
    if controls is None:
        _warn(echo, "switching isn't available in this session.")
    return controls


def _short(path: Path) -> str:
    try:
        rel = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    return f"~/{rel.as_posix()}"


def describe(settings: Any) -> str:
    """'anthropic · claude-x' — the label used in the banner, /status and here."""
    return " · ".join(part for part in (settings.provider, settings.model) if part)


# -- /model -------------------------------------------------------------------


def model_options(controls: Any, *, fetch: bool = True) -> list[Option]:
    """Everything /model can offer: this provider's models, then the others'."""
    settings = controls.settings
    options: list[Option] = []
    seen: set[str] = set()

    if fetch:
        controls.fetch_models()  # cached; afterwards known_models() includes them
    for model in controls.known_models():
        if model not in seen:
            seen.add(model)
            options.append(Option(model, hint="current" if model == settings.model else ""))

    for info in controls.providers():
        if info.current or not info.has_key:
            continue
        for model in controls.known_models(info.name):
            options.append(Option(f"{info.name}/{model}", label=model, hint=info.name))
    return options


def handle_model(runtime: Any, arg: str, echo: Echo, *, chooser: Chooser = pick) -> None:
    controls = _controls(runtime, echo)
    if controls is None:
        return
    choice = arg.strip()
    if not choice:
        settings = controls.settings
        if not controls.fetch_models():
            # e.g. an Azure deployment: the key can chat but can't enumerate.
            echo(_dim(
                f"  {settings.provider} doesn't list its models here — type a name, or add "
                f'models = ["…"] under [providers.{settings.provider}] to pin favourites.'
            ))
        options = model_options(controls)
        choice = chooser(
            f"model · {settings.provider}",
            options,
            current=settings.model,
            allow_custom=True,  # e.g. an Azure deployment name the API can't list
        ) or ""
        if not choice:
            echo(_dim("  model unchanged."))
            return
    _apply_model(controls, choice, echo)


def _apply_model(controls: Any, choice: str, echo: Echo, provider: str | None = None) -> None:
    try:
        settings = controls.set_model(choice, provider)
    except ConfigError as exc:
        _warn(echo, str(exc))
        return
    echo("  model → " + typer.style(describe(settings), fg=typer.colors.CYAN, bold=True) + _dim(_SESSION_NOTE))


# -- /provider ----------------------------------------------------------------


def handle_provider(runtime: Any, arg: str, echo: Echo, *, chooser: Chooser = pick) -> None:
    controls = _controls(runtime, echo)
    if controls is None:
        return
    choice = arg.strip()
    if not choice:
        options = [
            Option(
                info.name,
                hint=(info.model if info.has_key else f"no key — set {env_var_for(info.name)}")
                + ("  · current" if info.current else ""),
            )
            for info in controls.providers()
        ]
        choice = chooser("provider", options, current=controls.settings.provider) or ""
        if not choice:
            echo(_dim("  provider unchanged."))
            return
    try:
        settings = controls.set_provider(choice)
    except ConfigError as exc:
        _warn(echo, str(exc))
        return
    echo("  provider → " + typer.style(describe(settings), fg=typer.colors.CYAN, bold=True) + _dim(_SESSION_NOTE))


# -- /effort ------------------------------------------------------------------


def handle_effort(runtime: Any, arg: str, echo: Echo, *, chooser: Chooser = pick) -> None:
    controls = _controls(runtime, echo)
    if controls is None:
        return
    choice = arg.strip().lower()
    if not choice:
        options = [Option(e.value, hint=EFFORT_NOTES[e]) for e in Effort]
        choice = chooser("effort", options, current=controls.settings.effort.value) or ""
        if not choice:
            echo(_dim("  effort unchanged."))
            return
    try:
        effort = Effort(choice)
    except ValueError:
        echo("usage: /effort " + "|".join(e.value for e in Effort))
        return
    controls.set_effort(effort)
    echo("  effort → " + typer.style(f" {effort.value} ", fg=typer.colors.BLACK, bg=typer.colors.WHITE, bold=True)
         + _dim(f"  {EFFORT_NOTES[effort]}"))


# -- /mode --------------------------------------------------------------------


def handle_mode(runtime: Any, arg: str, echo: Echo, *, chooser: Chooser = pick) -> None:
    choice = arg.strip().lower()
    current = runtime.executor.permissions.mode
    if not choice:
        options = [Option(m.value, hint=MODE_NOTES[m]) for m in PermissionMode]
        choice = chooser("mode", options, current=getattr(current, "value", None)) or ""
        if not choice:
            echo("usage: /mode plan|auto|dangerous")
            return
    try:
        mode = PermissionMode(choice)
    except ValueError:
        echo("usage: /mode plan|auto|dangerous")
        return

    controls = getattr(runtime, "controls", None)
    if controls is not None:
        controls.set_mode(mode)  # also re-clamps sub-agents and re-renders the prompt
    else:
        runtime.executor.permissions.mode = mode
    if mode == PermissionMode.DANGEROUS:
        echo(typer.style("  " + DANGEROUS_WARNING, fg=typer.colors.RED, bold=True))
    # Tell the model about the change so its self-description stays accurate.
    runtime.context.add_user(f"[mode changed to {mode.value}]")
    echo("  mode → " + typer.style(f" {mode.value} ", fg=typer.colors.BLACK, bg=MODE_COLOR[mode], bold=True)
         + _dim(f"  {MODE_NOTES[mode]}"))


# -- /save --------------------------------------------------------------------


def handle_save(runtime: Any, echo: Echo, *, config_path: Path | None = None) -> None:
    controls = _controls(runtime, echo)
    if controls is None:
        return
    path = config_path or find_config_path(runtime.executor.ctx.sandbox.root)
    if path is None:
        _warn(echo, "no config file to save into — run: jarvis init")
        return
    settings = controls.settings
    try:
        save_defaults(path, provider=settings.provider, model=settings.model, effort=settings.effort)
    except ConfigError as exc:
        _warn(echo, str(exc))
        return
    echo(
        typer.style("  saved as default: ", fg=typer.colors.GREEN)
        + f"{describe(settings)} · effort {settings.effort.value}"
        + _dim(f"  ({_short(path)})")
    )


# -- tab completion -----------------------------------------------------------

COMMANDS = [
    "/help", "/status", "/model", "/provider", "/effort", "/mode", "/save",
    "/compress", "/clear", "/sandbox", "/talk", "/mic", "/exit",
]


def completions(buffer: str, runtime: Any) -> list[str]:
    """What Tab should offer for the line typed so far (never touches the network)."""
    if not buffer.startswith("/"):
        return []
    head, sep, rest = buffer.partition(" ")
    if not sep:
        return [c for c in COMMANDS if c.startswith(head)]
    if " " in rest.strip():
        return []  # these commands take a single value
    word = rest.strip()
    controls = getattr(runtime, "controls", None)
    values: list[str] = []
    if head == "/mode":
        values = [m.value for m in PermissionMode]
    elif head == "/effort":
        values = [e.value for e in Effort]
    elif controls is not None and head == "/provider":
        values = [i.name for i in controls.providers()]
    elif controls is not None and head == "/model":
        values = [o.value for o in model_options(controls, fetch=False)]
    return [v for v in values if v.lower().startswith(word.lower())]


def install_completer(get_runtime: Callable[[], Any]) -> bool:
    """Wire Tab completion for slash commands into readline. False if unavailable."""
    try:
        import readline
    except Exception:  # pragma: no cover - readline missing on some builds
        return False

    matches: list[str] = []

    def completer(text: str, state: int) -> str | None:
        nonlocal matches
        try:
            if state == 0:
                buffer = readline.get_line_buffer()
                matches = completions(buffer, get_runtime())
                if " " not in buffer:
                    # Completing the command itself: add the space for its value.
                    matches = [m + " " if m in ("/model", "/provider", "/effort", "/mode") else m
                               for m in matches]
            return matches[state] if state < len(matches) else None
        except Exception:  # a completer must never break the prompt
            return None

    try:
        readline.set_completer(completer)
        readline.set_completer_delims(" \t\n")  # "/" and "-" are part of a word here
        if "libedit" in (readline.__doc__ or ""):
            readline.parse_and_bind("bind ^I rl_complete")  # macOS
        else:
            readline.parse_and_bind("tab: complete")
    except Exception:  # pragma: no cover - exotic readline builds
        return False
    return True
