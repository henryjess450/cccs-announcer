"""Fan-out of status changes to every open browser via Server-Sent Events.

The player runs on its own thread, so publishing has to hop onto the asyncio
loop with call_soon_threadsafe. The most recent snapshot is cached so a browser
that connects mid-announcement immediately sees the true state instead of
waiting for the next change.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Set

log = logging.getLogger(__name__)

# If a client is so far behind that its buffer fills, it is not keeping up.
# Snapshots are absolute (not deltas), so dropping the stale ones is harmless.
QUEUE_SIZE = 8


class Broadcaster:
    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.latest: Dict[str, Any] = {}

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, payload: Dict[str, Any]) -> None:
        """Safe to call from any thread, including the player thread."""
        self.latest = payload
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._deliver, payload)
        except RuntimeError:
            # Loop shutting down; nothing useful to do.
            pass

    def _deliver(self, payload: Dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                log.debug("Dropping a status update for a slow client")


def sse_message(payload: Dict[str, Any], event: str = "status") -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
