"""The live instruction queue.

User text/voice arrives here (classified), and the agent loop drains it at safe
checkpoints. When a permission prompt is active, the prompter pulls the next
instruction directly (see ``QueueAwarePrompter``) and interprets it as a response.
"""

from __future__ import annotations

from collections import deque

from .bus import EventBus
from .classify import classify
from .types import EventType, Instruction, InstructionKind


class InstructionQueue:
    def __init__(self, bus: EventBus | None = None) -> None:
        self._items: deque[Instruction] = deque()
        self._bus = bus

    def submit(
        self,
        text: str,
        *,
        source: str = "text",
        kind: InstructionKind | None = None,
    ) -> Instruction:
        instr = Instruction(kind=kind or classify(text), text=text, source=source)
        self._items.append(instr)
        if self._bus is not None:
            if source == "voice":
                self._bus.emit(EventType.USER_VOICE_INPUT, text=text, kind=instr.kind.value)
            else:
                self._bus.emit(EventType.USER_TEXT_INPUT, text=text, kind=instr.kind.value)
            if instr.kind in (InstructionKind.HARD_INTERRUPT, InstructionKind.SOFT_INTERRUPT):
                self._bus.emit(EventType.INTERRUPT_REQUESTED, kind=instr.kind.value)
            elif instr.kind == InstructionKind.STEERING:
                self._bus.emit(EventType.STEERING_INSTRUCTION_RECEIVED, text=text)
        return instr

    def drain(self) -> list[Instruction]:
        """Pop and return all queued instructions in arrival order."""
        items = list(self._items)
        self._items.clear()
        return items

    def drain_steering(self) -> list[Instruction]:
        """Pop everything except permission responses.

        Permission responses are left queued so the active permission prompt (via
        ``QueueAwarePrompter``) consumes them — not the general loop checkpoint.
        """
        kept: deque[Instruction] = deque()
        out: list[Instruction] = []
        while self._items:
            instr = self._items.popleft()
            if instr.kind == InstructionKind.PERMISSION_RESPONSE:
                kept.append(instr)
            else:
                out.append(instr)
        self._items = kept
        return out

    def pop_next(self) -> Instruction | None:
        """Pop the next instruction regardless of kind (used by the prompter)."""
        return self._items.popleft() if self._items else None

    def has_pending(self) -> bool:
        return bool(self._items)

    def __len__(self) -> int:
        return len(self._items)
