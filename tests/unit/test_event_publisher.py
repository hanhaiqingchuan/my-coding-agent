from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from coding_agent.core.models import DurableEvent, RunState
from coding_agent.runtime.publisher import AssistantDelta, EventPublisher
from coding_agent.storage.sqlite import SQLiteStore


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
async def test_default_queue_drops_a_slow_consumer_while_fast_consumer_continues() -> None:
    """An unbounded default queue would let an abandoned browser consume memory forever."""
    publisher = EventPublisher()
    async with publisher.session_guard("session-a"):
        slow = publisher.subscribe_locked("session-a")
        fast = publisher.subscribe_locked("session-a")

    for seq in range(1, 2_049):
        event = _event("session-a", seq)
        await publisher.publish_committed(event)
        assert await fast.receive() == event
        if slow.closed:
            break

    assert slow.closed is True
    assert fast.closed is False


def test_non_positive_queue_size_is_rejected() -> None:
    """Allowing zero would silently restore asyncio's unbounded queue behavior."""
    with pytest.raises(ValueError, match="positive"):
        EventPublisher(queue_size=0)


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


@pytest.mark.asyncio
async def test_real_snapshot_cut_filters_committed_before_publish_without_losing_later_event(
    tmp_path,
) -> None:
    """A delayed publish must be absent when snapshotted and present when newer than the cut."""
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    session = store.create_session(str(tmp_path), "cut")
    run = store.begin_run(session.id, "task", {}, "start", "start-hash")
    publisher = EventPublisher()

    committed = asyncio.Event()
    allow_publish = asyncio.Event()

    async def commit_then_publish() -> DurableEvent:
        previous = store.load_snapshot(session.id).snapshot_seq
        store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
        event = store.events_after(session.id, previous)[0]
        committed.set()
        await allow_publish.wait()
        await publisher.publish_committed(event)
        return event

    delayed_publish = asyncio.create_task(commit_then_publish())
    await committed.wait()
    async with publisher.session_guard(session.id):
        snapshot = store.load_snapshot(session.id)
        subscription = publisher.subscribe_locked(session.id, after_seq=snapshot.snapshot_seq)
    allow_publish.set()
    duplicated = await delayed_publish

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(subscription.receive(), 0.02)

    async with publisher.session_guard(session.id):
        earlier_snapshot = store.load_snapshot(session.id)
        later_subscription = publisher.subscribe_locked(
            session.id, after_seq=earlier_snapshot.snapshot_seq
        )
        previous = earlier_snapshot.snapshot_seq
        later_committed = asyncio.Event()

        async def commit_after_cut() -> DurableEvent:
            store.transition_run(
                run.id, {RunState.BUILDING_CONTEXT}, RunState.MODEL_STREAMING, None, None
            )
            event = store.events_after(session.id, previous)[0]
            later_committed.set()
            await publisher.publish_committed(event)
            return event

        publish_later = asyncio.create_task(commit_after_cut())
        await later_committed.wait()
    later = await publish_later

    assert later.seq > earlier_snapshot.snapshot_seq
    assert await later_subscription.receive() == later
    assert duplicated.seq == snapshot.snapshot_seq
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(later_subscription.receive(), 0.02)


@pytest.mark.asyncio
async def test_transient_delta_does_not_advance_the_durable_sequence_cut() -> None:
    """Treating a delta as durable could make the next committed event disappear."""
    publisher = EventPublisher()
    async with publisher.session_guard("session-a"):
        subscription = publisher.subscribe_locked("session-a", after_seq=2)

    delta = AssistantDelta("session-a", "run-a", "epoch-a", 0, "draft")
    await publisher.publish_transient(delta)
    await publisher.publish_committed(_event("session-a", 2))
    next_event = _event("session-a", 3)
    await publisher.publish_committed(next_event)

    assert await subscription.receive() == delta
    assert await subscription.receive() == next_event
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(subscription.receive(), 0.02)
