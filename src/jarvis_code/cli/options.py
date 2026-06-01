"""Global CLI options shared by the callback and subcommands."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis_code.config import Effort
from jarvis_code.permissions import PermissionMode


@dataclass
class GlobalOptions:
    sandbox: str | None = None
    provider: str | None = None
    model: str | None = None
    mode: PermissionMode = PermissionMode.AUTO
    # None = use the config value; True/False = override voice output for this run.
    voice: bool | None = None
    # None = use the config value ([agent] effort); else override for this run.
    effort: Effort | None = None


def parse_mode(value: str) -> PermissionMode:
    try:
        return PermissionMode(value)
    except ValueError as exc:  # pragma: no cover - Typer validates the choices
        raise ValueError(f"invalid mode '{value}'") from exc


_TRUE = {"true", "t", "yes", "y", "on", "1"}
_FALSE = {"false", "f", "no", "n", "off", "0"}


def parse_voice(value: str | None) -> bool | None:
    """Parse the --voice value. None/empty -> use config; else true/false."""
    if value is None:
        return None
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f"invalid --voice value '{value}' (use true or false)")
