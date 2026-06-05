"""Permission modes and the static mode x capability policy matrix.

Exactly three modes exist. The policy is deterministic and lives here so the
allow/ask/deny behavior of every tool class is one table, easy to audit.
"""

from __future__ import annotations

from enum import Enum


class PermissionMode(str, Enum):
    PLAN = "plan"
    AUTO = "auto"
    DANGEROUS = "dangerous"


class Capability(str, Enum):
    """The permission class a tool falls into."""

    READ = "read"
    SEARCH = "search"
    GIT_READ = "git_read"
    WRITE = "write"
    EDIT = "edit"
    DELETE = "delete"
    MOVE = "move"
    SHELL = "shell"
    SUBAGENT = "subagent"
    ASK = "ask"


class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


# Read-only capabilities are always allowed (still sandbox-confined for fs ops).
_READ_ONLY = {Capability.READ, Capability.SEARCH, Capability.GIT_READ}
# Mutating + shell capabilities.
_MUTATING = {Capability.WRITE, Capability.EDIT, Capability.DELETE, Capability.MOVE}


def decide(mode: PermissionMode, capability: Capability) -> Decision:
    """Return the policy decision for a capability under a mode."""
    if mode == PermissionMode.DANGEROUS:
        # Everything allowed without prompting — but still sandbox-confined,
        # and catastrophic shell patterns are still screened out elsewhere.
        return Decision.ALLOW

    if capability in _READ_ONLY or capability in (Capability.SUBAGENT, Capability.ASK):
        # Sub-agents inherit the parent mode, so spawning never escalates. Asking
        # the user a question mutates nothing, so it is allowed in every mode.
        return Decision.ALLOW

    if mode == PermissionMode.PLAN:
        # Plan mode is read/inspect/search/plan only.
        return Decision.DENY

    # AUTO mode: writes/edits/deletes/moves and shell all require approval.
    return Decision.ASK
