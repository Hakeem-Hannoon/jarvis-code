"""Effort profiles: how hard the agent works.

Each :class:`~jarvis_code.config.Effort` level maps to a profile that tunes
*agent behavior* — step ceiling, response token budget, whether sub-agents are
available, and a one-line reasoning nudge added to the system prompt.

Deliberately NOT mapped here: provider-native "reasoning"/"thinking" parameters.
Those are model-gated (sending `reasoning_effort` or a thinking budget to a
model that doesn't support it returns a 400), so effort instead raises the token
budget and instructs the model to reason harder — which works on every provider.
Native reasoning-param mapping is a possible future enhancement, guarded per
model.
"""

from __future__ import annotations

from dataclasses import dataclass

from jarvis_code.config import Effort


@dataclass(frozen=True)
class EffortProfile:
    max_steps: int | None   # None = unlimited
    max_tokens: int         # response budget (room to think + write)
    use_subagents: bool     # may the model spawn sub-agents? (madmax only)
    guidance: str           # one-line system-prompt nudge


_PROFILES: dict[Effort, EffortProfile] = {
    Effort.LOW: EffortProfile(
        max_steps=15,
        max_tokens=2048,
        use_subagents=False,
        guidance=(
            "EFFORT low: be quick and minimal — make the smallest change that "
            "works, avoid extra exploration, and keep explanations short."
        ),
    ),
    Effort.MEDIUM: EffortProfile(
        max_steps=None,
        max_tokens=4096,
        use_subagents=False,
        guidance=(
            "EFFORT medium: balanced — solve the task directly and sanity-check "
            "your work before finishing."
        ),
    ),
    Effort.HIGH: EffortProfile(
        max_steps=None,
        max_tokens=8192,
        use_subagents=False,
        guidance=(
            "EFFORT high: be thorough — reason carefully about edge cases and "
            "verify your changes (run tests when possible) before finishing."
        ),
    ),
    Effort.MADMAX: EffortProfile(
        max_steps=None,
        max_tokens=8192,
        use_subagents=True,
        guidance=(
            "EFFORT madmax: maximum rigor — decompose hard problems, spawn focused "
            "sub-agents when they help, and verify thoroughly (run tests / review) "
            "before declaring the task done."
        ),
    ),
}


def profile_for(effort: Effort) -> EffortProfile:
    return _PROFILES[effort]
