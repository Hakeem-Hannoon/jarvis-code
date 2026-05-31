"""The ``jarvis`` command-line entry point.

Phase 0 ships a thin but real Typer surface so ``jarvis --help`` works and the
console-script wiring is verified. Later phases flesh out the command bodies
(init, doctor, config, voices) — the signatures here are intentionally stable
so the public CLI surface does not churn.
"""

from __future__ import annotations

import typer

from jarvis_code import __version__

app = typer.Typer(
    name="jarvis",
    help=(
        "jarvis — a local, sandboxed coding agent. The agent operates only "
        "inside the sandbox root (the launch directory, or --sandbox <path>). "
        "The model proposes actions; the runtime enforces the rules."
    ),
    no_args_is_help=False,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"jarvis-code {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the jarvis-code version and exit.",
    ),
) -> None:
    """jarvis — run with a task, or use a subcommand (init, doctor, config, voices).

    Full agent runtime, permission modes, and voice are wired up in later phases.
    """
    if ctx.invoked_subcommand is None:
        # No subcommand: in later phases this launches the interactive runtime.
        # For now, show help so first-run users see something useful.
        typer.echo(ctx.get_help())


if __name__ == "__main__":  # pragma: no cover
    app()
