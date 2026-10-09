"""`jarvis init` — scaffold a git-ignored .jarvis/ workspace."""

from __future__ import annotations

from pathlib import Path

import typer

from jarvis_code.config import DEFAULT_CONFIG_TEMPLATE, global_config_path
from jarvis_code.sandbox import Sandbox, SandboxRootError, resolve_sandbox_root

from .options import GlobalOptions


def run_init_global() -> int:
    """Create the per-user global config (~/.config/jarvis/config.toml)."""
    path = global_config_path()
    if path.exists():
        typer.echo(f"Global config already exists, leaving it untouched: {path}")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TEMPLATE)
    # The global config may hold a real key; restrict to the owner.
    try:
        path.chmod(0o600)
        path.parent.chmod(0o700)
    except OSError:  # pragma: no cover - non-POSIX or odd perms
        pass
    typer.echo(typer.style(f"Created {path}", fg=typer.colors.GREEN))
    typer.echo("Edit it (add a provider key); jarvis will use it from any directory")
    typer.echo("that has no project-local .jarvis/config.toml.")
    return 0


def run_init(opts: GlobalOptions, *, global_config: bool = False) -> int:
    if global_config:
        return run_init_global()
    try:
        root = resolve_sandbox_root(Path.cwd(), opts.sandbox)
    except SandboxRootError as exc:
        typer.echo(str(exc), err=True)
        return 2
    sandbox = Sandbox(root)

    for directory in (".jarvis", ".jarvis/sessions", ".jarvis/voices"):
        sandbox.mkdir(directory)

    # Make the whole .jarvis/ tree git-ignored by default, regardless of the
    # project's own .gitignore — this is what keeps real keys out of git.
    sandbox.write(".jarvis/.gitignore", "*\n")

    config_rel = ".jarvis/config.toml"
    if sandbox.exists(config_rel):
        typer.echo(f"Config already exists, leaving it untouched: {config_rel}")
    else:
        written = sandbox.write(config_rel, DEFAULT_CONFIG_TEMPLATE)
        # It is about to hold an API key: owner-only, like the global config.
        try:
            written.chmod(0o600)
        except OSError:  # pragma: no cover - non-POSIX or odd perms
            pass
        typer.echo(typer.style(f"Created {config_rel}", fg=typer.colors.GREEN))

    typer.echo(f"Created .jarvis/ at: {root}")
    typer.echo("  .jarvis/sessions/   (transcripts)")
    typer.echo("  .jarvis/voices/     (local Piper voice models)")
    typer.echo("")
    typer.echo("Next:")
    typer.echo("  1. Edit .jarvis/config.toml and add a provider API key")
    typer.echo("     (or export e.g. ANTHROPIC_API_KEY).")
    typer.echo("  2. Run: jarvis doctor")
    typer.echo('  3. Run: jarvis --mode plan "explain this project"')
    return 0
