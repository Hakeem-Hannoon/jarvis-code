"""The permission manager: turns a policy decision into an outcome.

Flow: a capability + a :class:`PermissionRequest` go in; the manager consults
the mode policy. ``ALLOW`` -> allowed; ``DENY`` -> denied; ``ASK`` -> consult a
:class:`Prompter` (the exact 1/2/3 menu) and map the answer to an outcome.
"Do Something Else" returns free-text feedback for the model to revise with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from jarvis_code.utils.logging import get_logger

from .modes import Capability, Decision, PermissionMode, decide

_log = get_logger("permissions")


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class PermissionRequest:
    """What the model proposes to do, with everything needed to decide.

    For shell, ``command``/``cwd``/``why``/``expected_effect``/``risk`` populate
    the prompt the instructions require. For edits/writes, ``diff`` carries the
    concise change preview.
    """

    tool_name: str
    capability: Capability
    summary: str
    command: str | None = None
    cwd: str | None = None
    why: str | None = None
    expected_effect: str | None = None
    risk: RiskLevel = RiskLevel.LOW
    diff: str | None = None


class Choice(str, Enum):
    YES = "yes"
    NO = "no"
    SOMETHING_ELSE = "something_else"


@dataclass
class PermissionResponse:
    """A prompter's answer. ``feedback`` is set only for SOMETHING_ELSE."""

    choice: Choice
    feedback: str | None = None


class OutcomeKind(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    REVISE = "revise"


@dataclass
class PermissionOutcome:
    kind: OutcomeKind
    reason: str | None = None
    feedback: str | None = None  # populated for REVISE

    @property
    def allowed(self) -> bool:
        return self.kind == OutcomeKind.ALLOWED


@runtime_checkable
class Prompter(Protocol):
    """Renders the 1/2/3 menu and collects the user's response."""

    def request_permission(self, request: PermissionRequest) -> PermissionResponse: ...


@dataclass
class PermissionManager:
    """Decides, per request, using the mode policy and (for ASK) a prompter."""

    mode: PermissionMode
    prompter: Prompter | None = None
    # Recorded decisions for the session transcript (no secrets here).
    history: list[dict] = field(default_factory=list)

    def decide(self, capability: Capability) -> Decision:
        return decide(self.mode, capability)

    def authorize(self, request: PermissionRequest) -> PermissionOutcome:
        decision = self.decide(request.capability)

        if decision == Decision.ALLOW:
            return self._record(request, OutcomeKind.ALLOWED, reason="policy allows")

        if decision == Decision.DENY:
            return self._record(
                request,
                OutcomeKind.DENIED,
                reason=f"{self.mode.value} mode does not allow '{request.capability.value}'",
            )

        # decision == ASK
        if self.prompter is None:
            # No way to ask → fail closed (deny), never silently allow.
            return self._record(
                request,
                OutcomeKind.DENIED,
                reason="approval required but no prompter is available",
            )

        response = self.prompter.request_permission(request)
        if response.choice == Choice.YES:
            return self._record(request, OutcomeKind.ALLOWED, reason="user approved")
        if response.choice == Choice.SOMETHING_ELSE:
            return self._record(
                request,
                OutcomeKind.REVISE,
                reason="user chose to redirect",
                feedback=response.feedback,
            )
        return self._record(request, OutcomeKind.DENIED, reason="user declined")

    def _record(
        self,
        request: PermissionRequest,
        kind: OutcomeKind,
        *,
        reason: str | None = None,
        feedback: str | None = None,
    ) -> PermissionOutcome:
        self.history.append(
            {
                "tool": request.tool_name,
                "capability": request.capability.value,
                "mode": self.mode.value,
                "outcome": kind.value,
                "reason": reason,
                # NB: feedback may contain user text but never secrets; the
                # transcript writer still routes everything through the redactor.
            }
        )
        _log.debug(
            "permission %s for %s (%s): %s",
            kind.value,
            request.tool_name,
            request.capability.value,
            reason,
        )
        return PermissionOutcome(kind=kind, reason=reason, feedback=feedback)
