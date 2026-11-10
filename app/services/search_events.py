"""In-process pub/sub for search progress events (consumed by the SSE route).

Each running search has a channel that keeps its full event history, so a
browser that connects late (or reconnects) replays everything it missed.
Channels are dropped a while after the search finishes; after that the SSE
route rebuilds the same events from the database.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class SearchEvent:
    type: str
    data: dict[str, Any]


@dataclass
class _Channel:
    history: list[SearchEvent] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    closed: bool = False


_CLOSE = object()


class SearchEventBroker:
    def __init__(self, retention_seconds: float = 300.0) -> None:
        self._channels: dict[int, _Channel] = {}
        self._retention = retention_seconds

    def open(self, search_id: int) -> None:
        self._channels.setdefault(search_id, _Channel())

    def is_active(self, search_id: int) -> bool:
        return search_id in self._channels

    def publish(self, search_id: int, event: SearchEvent) -> None:
        channel = self._channels.get(search_id)
        if channel is None or channel.closed:
            return
        channel.history.append(event)
        for queue in channel.subscribers:
            queue.put_nowait(event)

    def close(self, search_id: int) -> None:
        channel = self._channels.get(search_id)
        if channel is None or channel.closed:
            return
        channel.closed = True
        for queue in channel.subscribers:
            queue.put_nowait(_CLOSE)
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(self._retention, self._channels.pop, search_id, None)
        except RuntimeError:  # pragma: no cover - no loop (shutdown)
            self._channels.pop(search_id, None)

    async def subscribe(
        self, search_id: int, heartbeat_seconds: float = 15.0
    ) -> AsyncIterator[Optional[SearchEvent]]:
        """Yield past and future events; yields None as a heartbeat."""
        channel = self._channels.get(search_id)
        if channel is None:
            return
        queue: asyncio.Queue = asyncio.Queue()
        # Snapshot history and register atomically (no awaits in between).
        backlog = list(channel.history)
        closed = channel.closed
        if not closed:
            channel.subscribers.add(queue)
        try:
            for event in backlog:
                yield event
            if closed:
                return
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                except asyncio.TimeoutError:
                    yield None
                    continue
                if item is _CLOSE:
                    return
                yield item
        finally:
            channel.subscribers.discard(queue)
