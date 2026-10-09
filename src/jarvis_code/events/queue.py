"""The live instruction queue.

User text/voice arrives here (classified), and the agent loop drains it at safe
checkpoints. When a permission prompt is active, the prompter pulls the next
instruction directly (see ``QueueAwarePrompter``) and interprets it as a response.
"""

from __future__ import annotations

import threading
from collections import deque

from .bus import EventBus
from .classify import classify
from .types import EventType, Instruction, InstructionKind


class InstructionQueue:
    """Thread-safe queue: a live reader thread may ``submit`` while the agent
    loop ``drain``s at its checkpoints, so all access is guarded by a lock."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self._items: deque[Instruction] = deque()
        self._bus = bus
        self._lock = threading.Lock()

    def submit(
        self,
        text: str,
        *,
        source: str = "text",
        kind: InstructionKind | None = None,
    ) -> Instruction:
        instr = Instruction(kind=kind or classify(text), text=text, source=source)
        with self._lock:
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
        with self._lock:
            items = list(self._items)
            self._items.clear()
        return items

    def drain_steering(self) -> list[Instruction]:
        """Pop everything except permission responses.

        Permission responses are left queued so the active permission prompt (via
        ``QueueAwarePrompter``) consumes them — not the general loop checkpoint.
        """
        out: list[Instruction] = []
        with self._lock:
            # Mutate in place under the lock: rebinding ``self._items`` here would
            # drop anything a reader thread appended to the old deque meanwhile.
            kept = [i for i in self._items if i.kind == InstructionKind.PERMISSION_RESPONSE]
            out = [i for i in self._items if i.kind != InstructionKind.PERMISSION_RESPONSE]
            self._items.clear()
            self._items.extend(kept)
        return out

    def pop_next(self) -> Instruction | None:
        """Pop the next instruction regardless of kind (used by the prompter)."""
        with self._lock:
            return self._items.popleft() if self._items else None

    def has_hard_interrupt(self) -> bool:
        """True if a "stop" is waiting — lets a long model call be abandoned early."""
        with self._lock:
            return any(i.kind == InstructionKind.HARD_INTERRUPT for i in self._items)

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
