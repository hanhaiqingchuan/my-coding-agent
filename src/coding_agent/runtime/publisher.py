"""Session-scoped in-memory delivery of already committed durable events."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import AsyncIterator, TypeAlias

from coding_agent.core.models import DurableEvent

_guarded_sessions: ContextVar[frozenset[str]] = ContextVar(
    "publisher_guarded_sessions", default=frozenset()
)


@dataclass(frozen=True, slots=True)
class AssistantDelta:
    session_id: str
    run_id: str
    draft_epoch: str
    index: int
    text: str

    def __post_init__(self) -> None:
        if not self.session_id or not self.run_id or not self.draft_epoch:
            raise ValueError("assistant delta requires session, run, and draft epoch")
        if self.index < 0:
            raise ValueError("assistant delta index must not be negative")


@dataclass(frozen=True, slots=True)
class ToolOutputDelta:
    session_id: str
    run_id: str
    draft_epoch: str
    tool_call_id: str
    text: str

    def __post_init__(self) -> None:
        if not self.session_id or not self.run_id or not self.draft_epoch:
            raise ValueError("tool output delta requires session, run, and draft epoch")
        if not self.tool_call_id:
            raise ValueError("tool output delta requires a tool call id")


TransientDelta: TypeAlias = AssistantDelta | ToolOutputDelta
PublishedMessage: TypeAlias = DurableEvent | TransientDelta


@dataclass(eq=False, slots=True)
class EventSubscription:
    session_id: str
    _queue: asyncio.Queue[PublishedMessage]
    _last_seq: int
    _closed: bool = field(default=False, init=False)

    @property
    def closed(self) -> bool:
        return self._closed

    async def receive(self) -> PublishedMessage:
        return await self._queue.get()


class EventPublisher:
    """Keep delivery latency for one client independent from all other clients."""

    def __init__(self, *, queue_size: int = 256) -> None:
        if queue_size <= 0:
            raise ValueError("publisher queue size must be positive")
        self._queue_size = queue_size
        self._locks: dict[str, asyncio.Lock] = {}
        self._subscriptions: dict[str, set[EventSubscription]] = {}

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    @asynccontextmanager
    async def session_guard(self, session_id: str) -> AsyncIterator[None]:
        lock = self._lock(session_id)
        await lock.acquire()
        token = _guarded_sessions.set(_guarded_sessions.get() | {session_id})
        try:
            yield
        finally:
            _guarded_sessions.reset(token)
            lock.release()

    def subscribe_locked(self, session_id: str, *, after_seq: int = 0) -> EventSubscription:
        """Register while the caller holds the same lock used for snapshot publication."""
        if session_id not in _guarded_sessions.get():
            raise RuntimeError("subscribe_locked requires the session guard")
        if after_seq < 0:
            raise ValueError("subscription cut sequence must not be negative")
        subscription = EventSubscription(session_id, asyncio.Queue(self._queue_size), after_seq)
        self._subscriptions.setdefault(session_id, set()).add(subscription)
        return subscription

    async def publish_committed(self, event: DurableEvent) -> None:
        async with self.session_guard(event.session_id):
            subscribers = self._subscriptions.get(event.session_id, set())
            for subscription in tuple(subscribers):
                if event.seq <= subscription._last_seq:
                    continue
                try:
                    subscription._queue.put_nowait(event)
                except asyncio.QueueFull:
                    subscription._closed = True
                    subscribers.discard(subscription)
                else:
                    subscription._last_seq = event.seq

    async def publish_transient(self, delta: TransientDelta) -> None:
        """Broadcast a non-durable draft update without advancing a subscriber's seq."""
        async with self.session_guard(delta.session_id):
            subscribers = self._subscriptions.get(delta.session_id, set())
            for subscription in tuple(subscribers):
                try:
                    subscription._queue.put_nowait(delta)
                except asyncio.QueueFull:
                    subscription._closed = True
                    subscribers.discard(subscription)

    async def unsubscribe(self, subscription: EventSubscription) -> None:
        async with self.session_guard(subscription.session_id):
            subscribers = self._subscriptions.get(subscription.session_id)
            if subscribers is not None:
                subscribers.discard(subscription)
                if not subscribers:
                    self._subscriptions.pop(subscription.session_id, None)
            subscription._closed = True


__all__ = [
    "AssistantDelta",
    "EventPublisher",
    "EventSubscription",
    "PublishedMessage",
    "ToolOutputDelta",
    "TransientDelta",
]
