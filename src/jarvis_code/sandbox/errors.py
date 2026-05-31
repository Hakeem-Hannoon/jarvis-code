"""Typed sandbox errors.

``SandboxViolation`` is raised (and logged) whenever a path or working directory
would resolve outside the sandbox root. ``SandboxRootError`` is for invalid
sandbox roots at startup (missing dir, not a directory) — a configuration
problem, not an escape attempt.
"""

from __future__ import annotations


class SandboxError(Exception):
    """Base class for all sandbox errors."""


class SandboxRootError(SandboxError):
    """The configured sandbox root is invalid (missing, not a directory, ...)."""


class SandboxViolation(SandboxError):
    """An operation tried to touch a path resolving outside the sandbox root.

    Attributes
    ----------
    attempted:
        The raw path/string the caller asked for (untrusted model input).
    resolved:
        The canonical absolute path it resolved to (may be outside the root).
    root:
        The sandbox root the resolved path was checked against.
    """

    def __init__(
        self,
        message: str,
        *,
        attempted: str | None = None,
        resolved: str | None = None,
        root: str | None = None,
    ) -> None:
        super().__init__(message)
        self.attempted = attempted
        self.resolved = resolved
        self.root = root
