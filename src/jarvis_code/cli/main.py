"""The ``jarvis`` command-line entry point.

Commands:
    jarvis "<task>"                      run the agent on a task
    jarvis --mode plan|auto|dangerous    select permission mode
    jarvis --sandbox <path>              set the sandbox root
    jarvis init                          scaffold .jarvis/
    jarvis doctor                        environment + config health checks
    jarvis config path|validate          config helpers
    jarvis voices list|test              voice engine info
    jarvis models                        providers + models you can switch to

``jarvis "<task>"`` works via a default-command group: any leading token that is
not a known subcommand is treated as a task for the hidden ``run`` command.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from typing import Optional

from jarvis_code import __version__
from jarvis_code.config import Effort, find_config_path, validate_config_file
from jarvis_code.permissions import PermissionMode

from .chat import run_chat
from .doctor import run_doctor
from .init import run_init
from .models_cmd import run_models
from .options import GlobalOptions, parse_voice
from .run import run_task
from .voices import voices_list, voices_test

app = typer.Typer(
    name="jarvis",
    help=(
        "jarvis — a local, sandboxed coding agent. Run it bare for an interactive "
        "chat, or pass a task in quotes to run it once. The agent operates only "
        "inside the sandbox root (the launch directory, or --sandbox <path>). "
        "The model proposes actions; the runtime enforces the rules."
    ),
    epilog=(
        "Examples:\n\n"
        "\b\n"
        '  jarvis                                    start an interactive chat\n'
        '  jarvis "fix the failing test"             run one task, then exit\n'
        '  jarvis --mode plan "explain this repo"    read-only: look, don\'t touch\n'
        '  jarvis -m openai/gpt-5 -e high "..."      pick a model / work harder\n'
        '  jarvis init && jarvis doctor              first-time setup + health check'
    ),
    add_completion=False,
)

config_app = typer.Typer(help="Configuration helpers.", add_completion=False)
voices_app = typer.Typer(help="Voice engine info.", add_completion=False)
app.add_typer(config_app, name="config")
app.add_typer(voices_app, name="voices")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"jarvis-code {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show the jarvis-code version and exit.",
    ),
    sandbox: str = typer.Option(
        None, "--sandbox", help="Sandbox root (default: current directory)."
    ),
    provider: str = typer.Option(
        None, "--provider", "-p", help="Use this configured provider for the run."
    ),
    model: str = typer.Option(
        None, "--model", "-m",
        help="Use this model. 'provider/model' picks both, e.g. openai/gpt-5.",
    ),
    mode: PermissionMode = typer.Option(
        PermissionMode.AUTO, "--mode", case_sensitive=False,
        help="Permission mode: plan (read-only), auto (asks), or dangerous.",
    ),
    voice: str = typer.Option(
        None, "--voice",
        help="Voice output: true or false. Default: on (override the config).",
    ),
    effort: Optional[Effort] = typer.Option(
        None, "--effort", "-e", case_sensitive=False,
        help="How hard to work: low, medium, high, or madmax (sub-agents + max rigor).",
    ),
) -> None:
    """Run a task, start a chat, or use a subcommand (init, doctor, config, voices)."""
    try:
        voice_flag = parse_voice(voice)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    ctx.obj = GlobalOptions(
        sandbox=sandbox, provider=provider, model=model, mode=mode,
        voice=voice_flag, effort=effort,
    )
    if ctx.invoked_subcommand is None:
        # Bare `jarvis` with no task -> interactive chat session.
        raise typer.Exit(run_chat(ctx.obj))


@app.command("run", hidden=True)
def run_cmd(
    ctx: typer.Context,
    task_parts: list[str] = typer.Argument(None, help="The task for the agent."),
) -> None:
    """Run the agent on a task (also the default for `jarvis \"<task>\"`)."""
    opts: GlobalOptions = ctx.obj or GlobalOptions()
    task = " ".join(task_parts or []).strip()
    if not task:
        # `jarvis run` with no task behaves like bare `jarvis`: start a chat.
        raise typer.Exit(run_chat(opts))
    raise typer.Exit(run_task(opts, task))


@app.command("init")
def init_cmd(
    ctx: typer.Context,
    global_config: bool = typer.Option(
        False, "--global", "-g",
        help="Create a per-user global config (~/.config/jarvis/config.toml) "
        "used from any directory without a project-local config.",
    ),
) -> None:
    """Create a git-ignored .jarvis/ (or, with --global, a per-user config)."""
    raise typer.Exit(run_init(ctx.obj or GlobalOptions(), global_config=global_config))


@app.command("doctor")
def doctor_cmd(ctx: typer.Context) -> None:
    """Check Python, install, config, provider key (boolean only), voice, git."""
    raise typer.Exit(run_doctor(ctx.obj or GlobalOptions()))


@app.command("models")
def models_cmd(
    ctx: typer.Context,
    no_fetch: bool = typer.Option(
        False, "--offline", help="Only show what the config says; don't ask the providers."
    ),
) -> None:
    """List configured providers and the models you can switch to."""
    raise typer.Exit(run_models(ctx.obj or GlobalOptions(), fetch=not no_fetch))


@config_app.command("path")
def config_path_cmd(ctx: typer.Context) -> None:
    """Print the resolved config file path (or where to create it)."""
    opts: GlobalOptions = ctx.obj or GlobalOptions()
    base = Path(opts.sandbox) if opts.sandbox else Path.cwd()
    found = find_config_path(base)
    if found is not None:
        typer.echo(str(found))
    else:
        typer.echo(str(base / ".jarvis" / "config.toml") + "  (does not exist — run: jarvis init)")


@config_app.command("validate")
def config_validate_cmd(ctx: typer.Context) -> None:
    """Validate the config file and report problems (never prints keys)."""
    opts: GlobalOptions = ctx.obj or GlobalOptions()
    base = Path(opts.sandbox) if opts.sandbox else Path.cwd()
    problems = validate_config_file(base_dir=base)
    if not problems:
        typer.echo(typer.style("Config is valid.", fg=typer.colors.GREEN))
        raise typer.Exit(0)
    for problem in problems:
        typer.echo(typer.style(f"• {problem}", fg=typer.colors.YELLOW))
    raise typer.Exit(1)


@voices_app.command("list")
def voices_list_cmd(ctx: typer.Context) -> None:
    """List voice engines and their availability."""
    raise typer.Exit(voices_list(ctx.obj or GlobalOptions()))


@voices_app.command("test")
def voices_test_cmd(ctx: typer.Context) -> None:
    """Speak a short test phrase (degrades to silent Noop if no engine)."""
    raise typer.Exit(voices_test(ctx.obj or GlobalOptions()))


_SUBCOMMANDS = {"init", "doctor", "config", "voices", "models", "run"}
# Global options that consume the following token as their value.
_VALUE_OPTIONS = {
    "--sandbox", "--provider", "--model", "--mode", "--voice", "--effort", "-p", "-m", "-e",
}
_PASSTHROUGH = {"--help", "-h", "--version"}


def inject_default_command(argv: list[str]) -> list[str]:
    """Insert the hidden ``run`` command before a leading task token.

    Leaves a real subcommand (init/doctor/config/voices) and --help/--version
    untouched. Accounts for value-taking global options so e.g. the ``plan`` in
    ``--mode plan "task"`` is not mistaken for a command.
    """
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _PASSTHROUGH:
            return argv
        if token.startswith("--") and "=" in token:
            i += 1
            continue
        if token in _VALUE_OPTIONS:
            i += 2  # skip the option and its value
            continue
        if token.startswith("-"):
            i += 1
            continue
        # First bare (positional) token.
        if token in _SUBCOMMANDS:
            return argv
        return [*argv[:i], "run", *argv[i:]]
    return argv


def main() -> None:
    """Console-script entry point: route bare tasks to the agent, then dispatch."""
    sys.argv = [sys.argv[0], *inject_default_command(sys.argv[1:])]
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
