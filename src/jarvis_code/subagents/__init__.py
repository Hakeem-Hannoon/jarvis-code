"""Sub-agent orchestration: focused agents in isolated, permission-bounded contexts."""

from __future__ import annotations

from .orchestrator import SubAgentOrchestrator
from .types import AGENT_ROLES, AGENT_TYPES, clamp_mode

__all__ = ["SubAgentOrchestrator", "AGENT_ROLES", "AGENT_TYPES", "clamp_mode"]
