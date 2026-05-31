"""A tiny synchronous publish/subscribe event bus.

Handlers are called inline when an event is published. Keeping dispatch
synchronous avoids ordering surprises during the agent loop; handlers that need
to do async work can enqueue onto their own structures.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from jarvis_code.utils.logging import get_logger

from .types import Event, EventType

_log = get_logger("events")

Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[EventType, list[Handler]] = defaultdict(list)
        self._all: list[Handler] = []
        self.history: list[Event] = []

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        self._subs[event_type].append(handler)

    def subscribe_all(self, handler: Handler) -> None:
        self._all.append(handler)

    def publish(self, event: Event) -> None:
        self.history.append(event)
        for handler in (*self._subs.get(event.type, ()), *self._all):
            try:
                handler(event)
            except Exception as exc:  # a bad subscriber must not break the loop
                _log.warning("event handler error for %s: %s", event.type.value, exc)

    def emit(self, event_type: EventType, **data: object) -> None:
        self.publish(Event(type=event_type, data=dict(data)))
