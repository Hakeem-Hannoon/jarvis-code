"""`jarvis models` — what you can switch to, without opening a chat.

Lists every configured provider, whether it has a key (a boolean — never the
key), its default model, any favourites from the config, and the models its API
reports. Nothing here is required reading: ``/model`` in the chat shows the
same information as a picker.
"""

from __future__ import annotations

from pathlib import Path

import typer

from jarvis_code.config import ConfigError, env_var_for, load_config
from jarvis_code.models import build_provider
from jarvis_code.sandbox import SandboxRootError, resolve_sandbox_root
from jarvis_code.utils.text import sanitize_terminal

from .options import GlobalOptions

_MAX_LISTED = 40


def run_models(opts: GlobalOptions, *, fetch: bool = True) -> int:
    try:
        root = resolve_sandbox_root(Path.cwd(), opts.sandbox)
        config = load_config(base_dir=root)
    except (SandboxRootError, ConfigError) as exc:
        typer.echo(str(exc), err=True)
        return 1

    wanted = opts.provider
    if wanted and wanted not in config.providers:
        typer.echo(f"Provider '{wanted}' is not configured. Configured: {sorted(config.providers)}", err=True)
        return 1

    for name, pconf in sorted(config.providers.items()):
        if wanted and name != wanted:
            continue
        default = name == config.default_provider
        head = typer.style(name, fg=typer.colors.CYAN, bold=True)
        if default:
            head += typer.style("  (default provider)", dim=True)
        typer.echo(head)
        if not pconf.has_key():
            typer.echo(typer.style(f"  ✗ no key — set {env_var_for(name)} or add api_key to the config", fg=typer.colors.YELLOW))
            typer.echo("")
            continue
        typer.echo(f"  default model  {sanitize_terminal(pconf.model)}")
        if pconf.models:
            typer.echo("  favourites     " + ", ".join(sanitize_terminal(m) for m in pconf.models))
        if fetch:
            try:
                listed = build_provider(config, provider=name).list_models()
            except ConfigError as exc:
                listed = []
                typer.echo(typer.style(f"  {str(exc).splitlines()[0]}", fg=typer.colors.YELLOW))
            if listed:
                shown = ", ".join(sanitize_terminal(m) for m in listed[:_MAX_LISTED])
                more = f" … +{len(listed) - _MAX_LISTED} more" if len(listed) > _MAX_LISTED else ""
                typer.echo(typer.style(f"  available      {shown}{more}", dim=True))
            else:
                typer.echo(typer.style(
                    "  available      (this endpoint doesn't list models — use any name it accepts)",
                    dim=True,
                ))
        typer.echo("")

    typer.echo(typer.style(
        'Switch with:  jarvis -m <provider>/<model> "task"   ·   or /model inside the chat',
        dim=True,
    ))
    return 0
