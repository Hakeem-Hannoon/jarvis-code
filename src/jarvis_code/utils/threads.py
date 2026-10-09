"""Run blocking calls off the event loop without making them un-abandonable.

``asyncio.to_thread`` uses the loop's default executor, whose (non-daemon)
worker threads are *joined* when the loop shuts down. A blocking call that can't
be cancelled — a five-minute model request, ``input()`` waiting at a prompt —
therefore keeps the whole process alive after Ctrl-C until it returns on its
own. A daemon thread lets the caller stop waiting and move on (or exit) at once;
the abandoned call simply finishes in the background and its result is dropped.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any


def run_in_daemon_thread(fn: Callable[..., Any], *args: Any) -> asyncio.Future:
    """Start ``fn(*args)`` in a daemon thread; return a future for its result.

    Must be called from a running event loop. Cancelling (or simply dropping)
    the returned future abandons the call — nothing waits for the thread.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def deliver(result: Any, error: BaseException | None) -> None:
        if future.done():  # the waiter gave up (cancelled) — drop the result
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    def runner() -> None:
        try:
            result, error = fn(*args), None
        except BaseException as exc:  # delivered to the awaiting coroutine
            result, error = None, exc
        try:
            loop.call_soon_threadsafe(deliver, result, error)
        except RuntimeError:
            pass  # the loop is already closed: nobody is waiting any more

    threading.Thread(target=runner, daemon=True, name=f"jarvis-{fn.__name__}").start()
    return future
