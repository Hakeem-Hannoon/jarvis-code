"""Global CLI options shared by the callback and subcommands."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis_code.permissions import PermissionMode


@dataclass
class GlobalOptions:
    sandbox: str | None = None
    provider: str | None = None
    model: str | None = None
    mode: PermissionMode = PermissionMode.AUTO


def parse_mode(value: str) -> PermissionMode:
    try:
        return PermissionMode(value)
    except ValueError as exc:  # pragma: no cover - Typer validates the choices
        raise ValueError(f"invalid mode '{value}'") from exc
