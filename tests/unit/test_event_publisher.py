from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from coding_agent.core.models import DurableEvent
from coding_agent.runtime.publisher import EventPublisher


def _event(session_id: str, seq: int) -> DurableEvent:
    return DurableEvent(seq, session_id, None, "test.event", {"seq": seq}, datetime.now(UTC))


@pytest.mark.asyncio
async def test_subscriptions_receive_only_their_session_events_and_can_unsubscribe() -> None:
    """Broadcasting without a session filter would leak another session's durable events."""
    publisher = EventPublisher()
    async with publisher.session_guard("session-a"):
        subscription = publisher.subscribe_locked("session-a")

    other = _event("session-b", 1)
    expected = _event("session-a", 2)
    await publisher.publish_committed(other)
    await publisher.publish_committed(expected)

    assert await asyncio.wait_for(subscription.receive(), 0.1) == expected
    await publisher.unsubscribe(subscription)
    await publisher.publish_committed(_event("session-a", 3))
    assert subscription.closed is True


@pytest.mark.asyncio
async def test_slow_bounded_subscriber_is_dropped_without_blocking_fast_subscriber() -> None:
    """Awaiting a full subscriber queue would let one browser stop every live publisher."""
    publisher = EventPublisher(queue_size=1)
    async with publisher.session_guard("session-a"):
        slow = publisher.subscribe_locked("session-a")
        fast = publisher.subscribe_locked("session-a")

    first = _event("session-a", 1)
    second = _event("session-a", 2)
    third = _event("session-a", 3)
    await publisher.publish_committed(first)
    assert await fast.receive() == first
    await publisher.publish_committed(second)
    assert await fast.receive() == second
    await publisher.publish_committed(third)

    assert slow.closed is True
    assert await fast.receive() == third


@pytest.mark.asyncio
async def test_session_guard_serializes_snapshot_cut_registration_and_publish() -> None:
    """Publishing through a different lock could create a snapshot-to-live event gap."""
    publisher = EventPublisher()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def subscribe_during_snapshot():
        async with publisher.session_guard("session-a"):
            entered.set()
            await release.wait()
            return publisher.subscribe_locked("session-a")

    subscribe_task = asyncio.create_task(subscribe_during_snapshot())
    await entered.wait()
    event = _event("session-a", 1)
    publish_task = asyncio.create_task(publisher.publish_committed(event))
    await asyncio.sleep(0)
    assert publish_task.done() is False

    release.set()
    subscription = await subscribe_task
    await publish_task
    assert await subscription.receive() == event
