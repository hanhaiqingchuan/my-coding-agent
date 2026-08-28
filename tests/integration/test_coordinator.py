from __future__ import annotations

import asyncio
import sqlite3

import pytest

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.errors import CommandIdConflict, StoreError
from coding_agent.core.events import RunOutcome
from coding_agent.core.models import (
    ApprovalDecision,
    AssistantTurn,
    EffectStartResult,
    ModelStopReason,
    PreparedToolCall,
    RunState,
    TextPart,
    ToolCall,
    ToolError,
    ToolResult,
    ToolUsePart,
    Usage,
)
from coding_agent.runtime.approval import ApprovalGate
from coding_agent.runtime.coordinator import RunCoordinator, RunMutationGate
from coding_agent.runtime.publisher import EventPublisher
from coding_agent.storage.sqlite import SQLiteStore


@pytest.fixture
def store(tmp_path) -> SQLiteStore:
    result = SQLiteStore(tmp_path / "state.db")
    result.initialize()
    return result


def _approved_call(store: SQLiteStore, tool_name: str) -> tuple[str, str, str]:
    session = store.create_session("/tmp/workspace", "race")
    run = store.begin_run(session.id, "task", {}, "start", "start-hash")
    call_id = f"call-{tool_name}"
    turn = AssistantTurn(
        "turn-1",
        (ToolUsePart(ToolCall(call_id, tool_name, {"path": "a.txt"})),),
        ModelStopReason.TOOL_USE,
        Usage(),
    )
    store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
    store.transition_run(run.id, {RunState.BUILDING_CONTEXT}, RunState.MODEL_STREAMING, None, None)
    store.stage_tool_group(run.id, turn)
    store.resolve_approval(
        run.id,
        call_id,
        ApprovalDecision.APPROVE,
        f"approve-{tool_name}",
        f"approve-hash-{tool_name}",
    )
    return session.id, run.id, call_id


class _CancellationRunner:
    def __init__(self, gate: RunMutationGate) -> None:
        self._gate = gate
        self.calls: list[str] = []
        self.started = asyncio.Event()

    async def run(
        self,
        run_id: str,
        session_id: str,
        cancellation: CancellationToken,
    ) -> RunOutcome:
        self.calls.append(run_id)
        self.started.set()
        await cancellation.wait()
        return await self._gate.finish_run(run_id, RunOutcome.cancel())


class _FailingPublisher(EventPublisher):
    async def publish_committed(self, event) -> None:
        _ = event
        raise RuntimeError("publisher unavailable")


async def _wait_for_active_run(store: SQLiteStore, session_id: str) -> str:
    for _ in range(100):
        active = store.load_snapshot(session_id).active_run
        if active is not None:
            return active.id
        await asyncio.sleep(0)
    raise AssertionError("run was not durably created")


async def _wait_for_approval_status(
    store: SQLiteStore,
    tool_call_id: str,
    status: str,
) -> None:
    for _ in range(100):
        with store.connection() as connection:
            row = connection.execute(
                "SELECT approval_status FROM tool_executions WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
        if row is not None and row[0] == status:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"approval did not reach {status}")


def _pending_approval(store: SQLiteStore, suffix: str):
    session = store.create_session("/tmp/workspace", f"approval-{suffix}")
    run = store.begin_run(session.id, "task", {}, f"start-{suffix}", f"start-hash-{suffix}")
    store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
    store.transition_run(run.id, {RunState.BUILDING_CONTEXT}, RunState.MODEL_STREAMING, None, None)
    turn = AssistantTurn(
        f"turn-{suffix}",
        (
            ToolUsePart(
                ToolCall(
                    f"call-{suffix}",
                    "write_file",
                    {"operation": "write", "path": "a.txt", "content": "new"},
                )
            ),
        ),
        ModelStopReason.TOOL_USE,
        Usage(),
    )
    prepared = store.stage_tool_group(run.id, turn).calls[0]
    store.request_approval(run.id, prepared)
    return session, run, prepared


@pytest.mark.asyncio
async def test_cancelled_start_caller_cannot_leave_a_committed_run_without_owner(
    store: SQLiteStore,
) -> None:
    """Cancellation during blocked publication must not strand a committed active run."""
    session = store.create_session("/tmp/workspace", "cancelled-start")
    publisher = EventPublisher()
    gate = RunMutationGate(store, publisher)
    runner = _CancellationRunner(gate)
    coordinator = RunCoordinator(
        store=store,
        mutation_gate=gate,
        runner=runner,
        config_snapshot={},
    )

    async with publisher.session_guard(session.id):
        starting = asyncio.create_task(coordinator.start_run(session.id, "task", "start"))
        run_id = await _wait_for_active_run(store, session.id)
        starting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await starting

    await asyncio.wait_for(runner.started.wait(), timeout=0.1)
    await coordinator.stop_run(run_id, "stop")
    finished = await coordinator.wait_for_run(run_id)
    assert finished.state is RunState.CANCELLED


@pytest.mark.asyncio
async def test_failed_start_broadcast_does_not_prevent_run_ownership(
    store: SQLiteStore,
) -> None:
    """A transient publisher failure cannot turn a committed Run into an ownerless Run."""
    session = store.create_session("/tmp/workspace", "failed-start-publish")
    gate = RunMutationGate(store, _FailingPublisher())
    runner = _CancellationRunner(gate)
    coordinator = RunCoordinator(
        store=store,
        mutation_gate=gate,
        runner=runner,
        config_snapshot={},
    )

    run = await coordinator.start_run(session.id, "task", "start")

    await asyncio.wait_for(runner.started.wait(), timeout=0.1)
    await coordinator.stop_run(run.id, "stop")
    finished = await coordinator.wait_for_run(run.id)
    assert finished.state is RunState.CANCELLED


@pytest.mark.asyncio
async def test_cancelled_approval_caller_cannot_lose_a_committed_decision(
    store: SQLiteStore,
) -> None:
    """Cancellation during blocked publication must still wake the registered waiter."""
    session, run, prepared = _pending_approval(store, "cancelled-publish")
    publisher = EventPublisher()
    gate = RunMutationGate(store, publisher)
    approvals = ApprovalGate()
    coordinator = RunCoordinator(
        store=store,
        mutation_gate=gate,
        runner=_CancellationRunner(gate),
        config_snapshot={},
        approval_gate=approvals,
    )
    waiting = asyncio.create_task(approvals.request(prepared, CancellationToken()))
    await approvals.next_request()

    async with publisher.session_guard(session.id):
        resolving = asyncio.create_task(
            coordinator.resolve_approval(
                run.id,
                prepared.call.id,
                ApprovalDecision.APPROVE,
                "approve",
            )
        )
        await _wait_for_approval_status(store, prepared.call.id, "approved")
        resolving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await resolving

    assert await asyncio.wait_for(waiting, timeout=0.1) is ApprovalDecision.APPROVE


@pytest.mark.asyncio
async def test_failed_approval_broadcast_does_not_prevent_decision_delivery(
    store: SQLiteStore,
) -> None:
    """A publisher exception after commit must not leave the approval waiter blocked."""
    _, run, prepared = _pending_approval(store, "failed-publish")
    gate = RunMutationGate(store, _FailingPublisher())
    approvals = ApprovalGate()
    coordinator = RunCoordinator(
        store=store,
        mutation_gate=gate,
        runner=_CancellationRunner(gate),
        config_snapshot={},
        approval_gate=approvals,
    )
    waiting = asyncio.create_task(approvals.request(prepared, CancellationToken()))
    await approvals.next_request()

    await coordinator.resolve_approval(
        run.id,
        prepared.call.id,
        ApprovalDecision.APPROVE,
        "approve",
    )

    assert await asyncio.wait_for(waiting, timeout=0.1) is ApprovalDecision.APPROVE


@pytest.mark.asyncio
async def test_start_command_replay_owns_only_one_loop(store: SQLiteStore) -> None:
    """Starting a second loop for one command replay would duplicate model and tool effects."""
    session = store.create_session("/tmp/workspace", "idempotent")
    gate = RunMutationGate(store, EventPublisher())
    runner = _CancellationRunner(gate)
    coordinator = RunCoordinator(
        store=store,
        mutation_gate=gate,
        runner=runner,
        config_snapshot={"model": "scripted"},
    )

    first, replay = await asyncio.gather(
        coordinator.start_run(session.id, "task", "start-1"),
        coordinator.start_run(session.id, "task", "start-1"),
    )
    await runner.started.wait()

    assert replay.id == first.id
    assert runner.calls == [first.id]

    await coordinator.stop_run(first.id, "stop-1")
    await coordinator.wait_for_run(first.id)


@pytest.mark.asyncio
async def test_start_command_id_rejects_a_different_payload(store: SQLiteStore) -> None:
    """Reusing an id for changed content must not silently return or start the wrong run."""
    session = store.create_session("/tmp/workspace", "conflict")
    gate = RunMutationGate(store, EventPublisher())
    runner = _CancellationRunner(gate)
    coordinator = RunCoordinator(
        store=store,
        mutation_gate=gate,
        runner=runner,
        config_snapshot={},
    )
    run = await coordinator.start_run(session.id, "first", "start-1")

    with pytest.raises(CommandIdConflict):
        await coordinator.start_run(session.id, "changed", "start-1")

    await runner.started.wait()
    assert runner.calls == [run.id]
    await coordinator.stop_run(run.id, "stop-1")
    await coordinator.wait_for_run(run.id)


@pytest.mark.asyncio
async def test_start_replay_after_restart_ignores_changed_server_config(
    store: SQLiteStore,
) -> None:
    """Server configuration isn't client payload and must not conflict with a replayed command."""
    session = store.create_session("/tmp/workspace", "restart-replay")
    first_gate = RunMutationGate(store, EventPublisher())
    restarted_gate = RunMutationGate(store, EventPublisher())

    first = await first_gate.begin_run(
        session.id,
        "same task",
        {"model": "before-restart", "nested": {"max_tokens": 100}},
        "stable-command-id",
    )
    replay = await restarted_gate.begin_run(
        session.id,
        "same task",
        {"model": "after-restart", "nested": {"max_tokens": 200}},
        "stable-command-id",
    )

    assert replay.id == first.id
    assert replay.config_snapshot == {
        "model": "before-restart",
        "nested": {"max_tokens": 100},
    }
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM client_commands").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_stop_command_replay_emits_one_cancellation_event(store: SQLiteStore) -> None:
    """Replaying Stop must not create a second durable cancellation or second token owner."""
    session = store.create_session("/tmp/workspace", "stop-replay")
    gate = RunMutationGate(store, EventPublisher())
    runner = _CancellationRunner(gate)
    coordinator = RunCoordinator(
        store=store,
        mutation_gate=gate,
        runner=runner,
        config_snapshot={},
    )
    run = await coordinator.start_run(session.id, "task", "start")
    await runner.started.wait()

    first = await coordinator.stop_run(run.id, "same-stop")
    replay = await coordinator.stop_run(run.id, "same-stop")
    await coordinator.wait_for_run(run.id)

    assert replay.id == first.id
    assert replay.cancellation_requested_at == first.cancellation_requested_at
    event_types = [event.type for event in store.events_after(session.id, 0)]
    assert event_types.count("run.cancellation_requested") == 1


@pytest.mark.asyncio
async def test_stop_command_id_rejects_a_different_run_payload(store: SQLiteStore) -> None:
    """A stop receipt from an earlier Run must not be replayed for a later Run."""
    session = store.create_session("/tmp/workspace", "stop-conflict")
    gate = RunMutationGate(store, EventPublisher())
    runner = _CancellationRunner(gate)
    coordinator = RunCoordinator(
        store=store,
        mutation_gate=gate,
        runner=runner,
        config_snapshot={},
    )
    first = await coordinator.start_run(session.id, "first", "start-1")
    await runner.started.wait()
    await coordinator.stop_run(first.id, "same-stop")
    await coordinator.wait_for_run(first.id)
    second = await coordinator.start_run(session.id, "second", "start-2")
    for _ in range(100):
        if runner.calls == [first.id, second.id]:
            break
        await asyncio.sleep(0)
    assert runner.calls == [first.id, second.id]

    with pytest.raises(CommandIdConflict):
        await coordinator.stop_run(second.id, "same-stop")

    await coordinator.stop_run(second.id, "stop-2")
    await coordinator.wait_for_run(second.id)


@pytest.mark.asyncio
async def test_approval_command_replay_delivers_one_decision(store: SQLiteStore) -> None:
    """Replaying an approval receipt must not resolve the in-memory waiter twice."""
    session = store.create_session("/tmp/workspace", "approval")
    run = store.begin_run(session.id, "task", {}, "start", "start-hash")
    store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
    store.transition_run(run.id, {RunState.BUILDING_CONTEXT}, RunState.MODEL_STREAMING, None, None)
    turn = AssistantTurn(
        "turn-approval",
        (
            ToolUsePart(
                ToolCall(
                    "call-write",
                    "write_file",
                    {"operation": "write", "path": "a.txt", "content": "new"},
                )
            ),
        ),
        ModelStopReason.TOOL_USE,
        Usage(),
    )
    prepared = store.stage_tool_group(run.id, turn).calls[0]
    assert isinstance(prepared, PreparedToolCall)
    store.request_approval(run.id, prepared)
    gate = RunMutationGate(store, EventPublisher())
    approvals = ApprovalGate()
    coordinator = RunCoordinator(
        store=store,
        mutation_gate=gate,
        runner=_CancellationRunner(gate),
        config_snapshot={},
        approval_gate=approvals,
    )
    waiting = asyncio.create_task(approvals.request(prepared, CancellationToken()))
    await approvals.next_request()

    first = await coordinator.resolve_approval(
        run.id,
        prepared.call.id,
        ApprovalDecision.APPROVE,
        "approval-1",
    )
    replay = await coordinator.resolve_approval(
        run.id,
        prepared.call.id,
        ApprovalDecision.APPROVE,
        "approval-1",
    )

    assert first is None
    assert replay is None
    assert await waiting is ApprovalDecision.APPROVE
    event_types = [event.type for event in store.events_after(session.id, 0)]
    assert event_types.count("approval.resolved") == 1


@pytest.mark.asyncio
async def test_approval_command_id_rejects_a_changed_decision(store: SQLiteStore) -> None:
    """Changing a replayed approval payload must not reinterpret the audited decision."""
    session = store.create_session("/tmp/workspace", "approval-conflict")
    run = store.begin_run(session.id, "task", {}, "start", "start-hash")
    store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
    store.transition_run(run.id, {RunState.BUILDING_CONTEXT}, RunState.MODEL_STREAMING, None, None)
    turn = AssistantTurn(
        "turn-approval-conflict",
        (
            ToolUsePart(
                ToolCall(
                    "call-write-conflict",
                    "write_file",
                    {"operation": "write", "path": "a.txt", "content": "new"},
                )
            ),
        ),
        ModelStopReason.TOOL_USE,
        Usage(),
    )
    prepared = store.stage_tool_group(run.id, turn).calls[0]
    store.request_approval(run.id, prepared)
    gate = RunMutationGate(store, EventPublisher())
    approvals = ApprovalGate()
    coordinator = RunCoordinator(
        store=store,
        mutation_gate=gate,
        runner=_CancellationRunner(gate),
        config_snapshot={},
        approval_gate=approvals,
    )
    waiting = asyncio.create_task(approvals.request(prepared, CancellationToken()))
    await approvals.next_request()
    await coordinator.resolve_approval(
        run.id,
        prepared.call.id,
        ApprovalDecision.APPROVE,
        "approval-conflict",
    )

    with pytest.raises(CommandIdConflict):
        await coordinator.resolve_approval(
            run.id,
            prepared.call.id,
            ApprovalDecision.REJECT,
            "approval-conflict",
        )

    assert await waiting is ApprovalDecision.APPROVE


@pytest.mark.asyncio
async def test_persisted_approval_is_not_lost_before_waiter_registration(
    store: SQLiteStore,
) -> None:
    """A fast client may approve after the durable event but before the loop starts waiting."""
    session = store.create_session("/tmp/workspace", "early-approval")
    run = store.begin_run(session.id, "task", {}, "start", "start-hash")
    store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
    store.transition_run(run.id, {RunState.BUILDING_CONTEXT}, RunState.MODEL_STREAMING, None, None)
    turn = AssistantTurn(
        "turn-early-approval",
        (
            ToolUsePart(
                ToolCall(
                    "call-early-write",
                    "write_file",
                    {"operation": "write", "path": "a.txt", "content": "new"},
                )
            ),
        ),
        ModelStopReason.TOOL_USE,
        Usage(),
    )
    prepared = store.stage_tool_group(run.id, turn).calls[0]
    store.request_approval(run.id, prepared)
    gate = RunMutationGate(store, EventPublisher())
    approvals = ApprovalGate()
    coordinator = RunCoordinator(
        store=store,
        mutation_gate=gate,
        runner=_CancellationRunner(gate),
        config_snapshot={},
        approval_gate=approvals,
    )

    await coordinator.resolve_approval(
        run.id,
        prepared.call.id,
        ApprovalDecision.APPROVE,
        "early-approval",
    )

    decision = await asyncio.wait_for(
        approvals.request(prepared, CancellationToken()),
        timeout=0.02,
    )
    assert decision is ApprovalDecision.APPROVE


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["write_file", "run_command"])
@pytest.mark.parametrize("winner", ["approve-first", "stop-first"])
async def test_stop_and_effect_start_have_one_linearized_winner(
    store: SQLiteStore, tool_name: str, winner: str
) -> None:
    """Checking cancellation outside the mutation gate could start an effect after Stop wins."""
    session_id, run_id, call_id = _approved_call(store, tool_name)
    token = CancellationToken()
    gate = RunMutationGate(store, EventPublisher())
    await gate.register_cancellation(run_id, token)

    if winner == "approve-first":
        effect_task = asyncio.create_task(gate.begin_effect(run_id, call_id))
        await asyncio.sleep(0)
        stop_task = asyncio.create_task(gate.request_stop(run_id, "stop"))
    else:
        stop_task = asyncio.create_task(gate.request_stop(run_id, "stop"))
        await asyncio.sleep(0)
        effect_task = asyncio.create_task(gate.begin_effect(run_id, call_id))

    effect, stopped = await asyncio.gather(effect_task, stop_task)

    assert stopped.state is RunState.CANCELLING
    assert token.cancelled is True
    assert effect is (
        EffectStartResult.STARTED if winner == "approve-first" else EffectStartResult.CANCELLING
    )
    with store.connection() as connection:
        row = connection.execute(
            "SELECT effect_started_at, execution_state FROM tool_executions WHERE tool_call_id = ?",
            (call_id,),
        ).fetchone()
    assert (row[0] is not None) is (winner == "approve-first")
    assert session_id


@pytest.mark.asyncio
async def test_failed_store_commit_is_never_broadcast(store: SQLiteStore) -> None:
    """Publishing before SQLite commit would expose an effect start that was rolled back."""
    session_id, run_id, call_id = _approved_call(store, "write_file")
    publisher = EventPublisher()
    async with publisher.session_guard(session_id):
        subscription = publisher.subscribe_locked(session_id)
    gate = RunMutationGate(store, publisher)
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_effect_event BEFORE INSERT ON events
            WHEN NEW.type = 'tool.effect_started'
            BEGIN SELECT RAISE(ABORT, 'injected effect failure'); END
            """
        )

    with pytest.raises(sqlite3.DatabaseError, match="injected effect failure"):
        await gate.begin_effect(run_id, call_id)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(subscription.receive(), 0.02)


@pytest.mark.asyncio
async def test_global_active_run_gate_rejects_the_second_session(store: SQLiteStore) -> None:
    """A per-session lock would allow two model/tool loops to mutate workspaces concurrently."""
    first = store.create_session("/tmp/one", "one")
    second = store.create_session("/tmp/two", "two")
    gate = RunMutationGate(store, EventPublisher())

    results = await asyncio.gather(
        gate.begin_run(first.id, "first", {}, "start-one"),
        gate.begin_run(second.id, "second", {}, "start-two"),
        return_exceptions=True,
    )

    assert sum(not isinstance(item, BaseException) for item in results) == 1
    error = next(item for item in results if isinstance(item, StoreError))
    assert error.code == "RUN_ALREADY_ACTIVE"


@pytest.mark.asyncio
async def test_started_cancelled_effect_keeps_cancelled_execution_state(
    store: SQLiteStore,
) -> None:
    """Mapping every failed envelope to failed would erase a command's actual cancellation."""
    _, run_id, call_id = _approved_call(store, "run_command")
    assert store.begin_effect(run_id, call_id) is EffectStartResult.STARTED
    store.settle_tool_group(
        "turn-1",
        (
            ToolResult(
                call_id,
                "cancelled",
                False,
                ToolError("COMMAND_CANCELLED", "cancelled"),
            ),
        ),
    )

    with store.connection() as connection:
        state = connection.execute(
            "SELECT execution_state FROM tool_executions WHERE tool_call_id = ?", (call_id,)
        ).fetchone()[0]
    assert state == "cancelled"


@pytest.mark.asyncio
async def test_final_message_and_terminal_state_roll_back_together(store: SQLiteStore) -> None:
    """A crash between final-message commit and run completion would create a false final answer."""
    session = store.create_session("/tmp/workspace", "final")
    run = store.begin_run(session.id, "task", {}, "start", "start-hash")
    store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
    store.transition_run(run.id, {RunState.BUILDING_CONTEXT}, RunState.MODEL_STREAMING, None, None)
    turn = AssistantTurn(
        "final-turn",
        (TextPart("done"),),
        ModelStopReason.END_TURN,
        Usage(),
    )
    gate = RunMutationGate(store, EventPublisher())
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_final_state_event BEFORE INSERT ON events
            WHEN NEW.type = 'run.state_changed'
            BEGIN SELECT RAISE(ABORT, 'injected final failure'); END
            """
        )

    with pytest.raises(sqlite3.DatabaseError, match="injected final failure"):
        await gate.commit_final_turn(run.id, turn, RunOutcome.complete())

    assert [message.role for message in store.load_committed_transcript(session.id)] == ["user"]
    assert store.get_run(run.id).state is RunState.MODEL_STREAMING
