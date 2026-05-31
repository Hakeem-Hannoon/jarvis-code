"""Append-only JSONL session transcripts.

Every record is routed through :func:`redact` before it touches disk, so a
registered key can never appear in a transcript. We also only ever record the
fields below — never raw secrets, never the environment.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from jarvis_code.sandbox import Sandbox
from jarvis_code.utils.logging import get_logger, redact

_log = get_logger("sessions")

_SESSION_DIR = ".jarvis/sessions"


def _redact_value(value: object) -> object:
    """Recursively scrub registered secrets from any string in a record."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def new_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class SessionWriter:
    """Writes one JSONL transcript per run, through the sandbox."""

    def __init__(
        self,
        sandbox: Sandbox,
        *,
        session_id: str | None = None,
        session_dir: str = _SESSION_DIR,
    ) -> None:
        self.sandbox = sandbox
        self.session_id = session_id or new_session_id()
        self.path = f"{session_dir}/{self.session_id}.jsonl"

    # -- low level ---------------------------------------------------------

    def record(self, kind: str, **fields: object) -> None:
        payload = {
            "type": kind,
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": self.session_id,
            **fields,
        }
        safe = _redact_value(payload)
        try:
            self.sandbox.append(self.path, json.dumps(safe, ensure_ascii=False) + "\n")
        except OSError as exc:  # pragma: no cover - disk issues shouldn't crash a run
            _log.warning("could not write transcript: %s", exc)

    # -- typed records -----------------------------------------------------

    def prompt(self, text: str) -> None:
        self.record("prompt", text=text)

    def model_response(self, text: str, tool_calls: list, stop_reason: str | None) -> None:
        self.record(
            "model_response",
            text=text,
            tool_calls=[{"name": tc.name, "arguments": tc.arguments} for tc in tool_calls],
            stop_reason=stop_reason,
        )

    def permission_decision(self, tool: str, capability: str, outcome: str, reason: str | None) -> None:
        self.record(
            "permission_decision",
            tool=tool,
            capability=capability,
            outcome=outcome,
            reason=reason,
        )

    def tool_result(self, name: str, ok: bool, output: str, error: str | None, metadata: dict) -> None:
        self.record(
            "tool_result",
            name=name,
            ok=ok,
            output=output,
            error=error,
            files_changed=metadata.get("files_changed"),
            command=metadata.get("command"),
            returncode=metadata.get("returncode"),
        )

    def steering(self, text: str) -> None:
        self.record("steering", text=text)

    def subagent_summary(self, agent_type: str, summary: str) -> None:
        self.record("subagent_summary", agent_type=agent_type, summary=summary)

    def error(self, message: str) -> None:
        self.record("error", message=message)

    def run_end(self, status: str, summary: str) -> None:
        self.record("run_end", status=status, summary=summary)
