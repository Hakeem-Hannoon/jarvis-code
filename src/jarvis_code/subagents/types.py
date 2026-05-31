"""Sub-agent types and permission-clamping (never exceed the parent)."""

from __future__ import annotations

from jarvis_code.permissions import PermissionMode

AGENT_TYPES = ("explorer", "planner", "debugger", "reviewer", "implementer", "verifier")

AGENT_ROLES: dict[str, str] = {
    "explorer": (
        "You are the explorer sub-agent. Map the relevant part of the codebase and "
        "report its structure, key files, and entry points. Read-only mindset."
    ),
    "planner": (
        "You are the planner sub-agent. Produce a concrete, ordered, minimal plan "
        "for the task. Do not make changes — return the plan as your summary."
    ),
    "debugger": (
        "You are the debugger sub-agent. Find the root cause of the issue, gather "
        "evidence (failing tests, stack traces), and report the cause concisely."
    ),
    "reviewer": (
        "You are the reviewer sub-agent. Review the code/changes for correctness, "
        "risks, and simplifications. Report findings; do not rewrite broadly."
    ),
    "implementer": (
        "You are the implementer sub-agent. Make the requested change with the "
        "smallest clear edit, then summarize exactly what you changed."
    ),
    "verifier": (
        "You are the verifier sub-agent. Verify behavior by running the relevant "
        "checks/tests and report pass/fail with the key evidence."
    ),
}

# plan < auto < dangerous
_RANK = {PermissionMode.PLAN: 0, PermissionMode.AUTO: 1, PermissionMode.DANGEROUS: 2}


def clamp_mode(parent: PermissionMode, requested: PermissionMode | None = None) -> PermissionMode:
    """Return the effective sub-agent mode — never more permissive than parent."""
    if requested is None:
        return parent
    return requested if _RANK[requested] <= _RANK[parent] else parent
