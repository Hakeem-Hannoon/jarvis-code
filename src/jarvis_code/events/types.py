"""Event types and the instruction-classification taxonomy."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EventType(str, Enum):
    USER_TEXT_INPUT = "user_text_input"
    USER_VOICE_INPUT = "user_voice_input"
    VOICE_MODE_CHANGED = "voice_mode_changed"
    PERMISSION_DECISION = "permission_decision"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    TOOL_FAILED = "tool_failed"
    AGENT_STEP_STARTED = "agent_step_started"
    AGENT_STEP_FINISHED = "agent_step_finished"
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_FINISHED = "subagent_finished"
    INTERRUPT_REQUESTED = "interrupt_requested"
    STEERING_INSTRUCTION_RECEIVED = "steering_instruction_received"


@dataclass
class Event:
    type: EventType
    data: dict = field(default_factory=dict)


class InstructionKind(str, Enum):
    """How a live user instruction should be handled."""

    STEERING = "steering"            # fold into the plan asap
    QUESTION = "question"            # answer briefly, keep working
    SOFT_INTERRUPT = "soft_interrupt"  # finish current safe step, then adjust
    HARD_INTERRUPT = "hard_interrupt"  # stop current action if safe
    PERMISSION_RESPONSE = "permission_response"  # route to the active prompt


@dataclass
class Instruction:
    kind: InstructionKind
    text: str
    source: str = "text"  # "text" or "voice"
