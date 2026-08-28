from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from coding_agent.core.errors import CommandIdConflict, StoreError
from coding_agent.core.models import (
    ApprovalDecision,
    ApprovalStatus,
    AssistantTurn,
    EffectStartResult,
    ModelStopReason,
    RunState,
    StopReason,
    TextPart,
    ToolCall,
    ToolExecutionState,
    ToolResult,
    ToolUsePart,
    Usage,
)
from coding_agent.storage.sqlite import SQLiteStore


@pytest.fixture
def store(tmp_path) -> SQLiteStore:
    result = SQLiteStore(tmp_path / "state.db")
    result.initialize()
    return result


@pytest.fixture
def session(store: SQLiteStore):
    return store.create_session("/tmp/workspace", "Recovery")


def two_write_calls() -> AssistantTurn:
    return AssistantTurn(
        id="assistant-tools",
        parts=(
            TextPart("I'll make two changes."),
            ToolUsePart(ToolCall("call-current", "write_file", {"path": "one.txt"})),
            ToolUsePart(ToolCall("call-later", "run_command", {"command": "true"})),
        ),
        stop_reason=ModelStopReason.TOOL_USE,
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def start_pending_group(store: SQLiteStore, session):
    run = store.begin_run(session.id, "change files", {}, "start-1", "start-hash")
    group = store.stage_tool_group(run.id, two_write_calls())
    return run, group


def test_cancellation_receipt_state_and_event_are_atomic_and_idempotent(
    store: SQLiteStore, session
) -> None:
    run = store.begin_run(session.id, "task", {}, "start", "start-hash")

    first = store.request_cancellation(run.id, "stop", "stop-hash")
    second = store.request_cancellation(run.id, "stop", "stop-hash")

    assert first.state is RunState.CANCELLING
    assert second.cancellation_requested_at == first.cancellation_requested_at
    assert [event.type for event in store.events_after(session.id, 0)].count(
        "run.cancellation_requested"
    ) == 1
    with pytest.raises(CommandIdConflict):
        store.request_cancellation(run.id, "stop", "different-hash")


def test_cancellation_rolls_back_if_its_durable_event_fails(store: SQLiteStore, session) -> None:
    run = store.begin_run(session.id, "task", {}, "start", "start-hash")
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_stop_event BEFORE INSERT ON events
            WHEN NEW.type = 'run.cancellation_requested'
            BEGIN SELECT RAISE(ABORT, 'injected stop failure'); END
            """
        )

    with pytest.raises(sqlite3.DatabaseError, match="injected stop failure"):
        store.request_cancellation(run.id, "stop", "stop-hash")

    snapshot = store.load_snapshot(session.id)
    assert snapshot.active_run.state is RunState.STARTING
    assert snapshot.active_run.cancellation_requested_at is None
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM client_commands WHERE client_command_id = 'stop'"
            ).fetchone()[0]
            == 0
        )


def test_approval_receipt_and_begin_effect_require_current_valid_approval(
    store: SQLiteStore, session
) -> None:
    run, _ = start_pending_group(store, session)

    approval = store.resolve_approval(
        run.id,
        "call-current",
        ApprovalDecision.APPROVE,
        "approve-1",
        "approve-hash",
    )
    duplicate = store.resolve_approval(
        run.id,
        "call-current",
        ApprovalDecision.APPROVE,
        "approve-1",
        "approve-hash",
    )

    assert approval.status is ApprovalStatus.APPROVED
    assert duplicate == approval
    assert store.begin_effect(run.id, "call-later") is EffectStartResult.NOT_ACTIVE
    assert store.begin_effect(run.id, "call-current") is EffectStartResult.STARTED
    assert store.begin_effect(run.id, "call-current") is EffectStartResult.NOT_ACTIVE
    with store.connection() as connection:
        row = connection.execute(
            "SELECT effect_started_at, execution_state FROM tool_executions WHERE tool_call_id = ?",
            ("call-current",),
        ).fetchone()
        assert row[0] is not None
        assert row[1] == ToolExecutionState.RUNNING.value


def test_approval_and_effect_each_roll_back_when_their_event_fails(
    store: SQLiteStore, session
) -> None:
    run, _ = start_pending_group(store, session)
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_approval_event BEFORE INSERT ON events
            WHEN NEW.type = 'approval.resolved'
            BEGIN SELECT RAISE(ABORT, 'injected approval failure'); END
            """
        )

    with pytest.raises(sqlite3.DatabaseError, match="injected approval failure"):
        store.resolve_approval(
            run.id,
            "call-current",
            ApprovalDecision.APPROVE,
            "approve-1",
            "approve-hash",
        )
    with store.connection() as connection:
        row = connection.execute(
            "SELECT approval_status FROM tool_executions WHERE tool_call_id = 'call-current'"
        ).fetchone()
        assert row[0] == ApprovalStatus.PENDING.value
        connection.execute("DROP TRIGGER fail_approval_event")

    store.resolve_approval(
        run.id,
        "call-current",
        ApprovalDecision.APPROVE,
        "approve-1",
        "approve-hash",
    )
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_effect_event BEFORE INSERT ON events
            WHEN NEW.type = 'tool.effect_started'
            BEGIN SELECT RAISE(ABORT, 'injected effect failure'); END
            """
        )

    with pytest.raises(sqlite3.DatabaseError, match="injected effect failure"):
        store.begin_effect(run.id, "call-current")
    with store.connection() as connection:
        row = connection.execute(
            "SELECT effect_started_at, execution_state FROM tool_executions WHERE tool_call_id = ?",
            ("call-current",),
        ).fetchone()
        assert tuple(row) == (None, ToolExecutionState.QUEUED.value)


def test_reject_records_rejected_result_and_skips_the_remaining_group(
    store: SQLiteStore, session
) -> None:
    run, _ = start_pending_group(store, session)

    approval = store.resolve_approval(
        run.id,
        "call-current",
        ApprovalDecision.REJECT,
        "reject-1",
        "reject-hash",
    )

    assert approval.status is ApprovalStatus.REJECTED
    history = store.load_committed_transcript(session.id)
    assert [message.role for message in history] == ["user", "assistant", "tool", "tool"]
    first_result = history[2].parts[0]
    second_result = history[3].parts[0]
    assert first_result.error.code == "TOOL_REJECTED"
    assert second_result.error.code == "TOOL_SKIPPED"


def test_begin_effect_and_cancellation_are_linearized_by_database_cas(
    store: SQLiteStore, session
) -> None:
    run, _ = start_pending_group(store, session)
    store.resolve_approval(
        run.id,
        "call-current",
        ApprovalDecision.APPROVE,
        "approve-1",
        "approve-hash",
    )
    barrier = threading.Barrier(2)

    def start_effect() -> EffectStartResult:
        barrier.wait()
        return store.begin_effect(run.id, "call-current")

    def stop_run():
        barrier.wait()
        return store.request_cancellation(run.id, "stop-1", "stop-hash")

    with ThreadPoolExecutor(max_workers=2) as executor:
        effect_future = executor.submit(start_effect)
        stop_future = executor.submit(stop_run)
        effect_result = effect_future.result()
        stopped = stop_future.result()

    assert stopped.state is RunState.CANCELLING
    assert effect_result in {EffectStartResult.STARTED, EffectStartResult.CANCELLING}
    with store.connection() as connection:
        row = connection.execute(
            "SELECT effect_started_at, execution_state FROM tool_executions WHERE tool_call_id = ?",
            ("call-current",),
        ).fetchone()
    if effect_result is EffectStartResult.STARTED:
        assert row[0] is not None
        assert row[1] == ToolExecutionState.RUNNING.value
    else:
        assert row[0] is None
        assert row[1] == ToolExecutionState.CANCELLED.value


def test_cancellation_after_effect_start_preserves_running_and_skips_later_call(
    store: SQLiteStore, session
) -> None:
    run, group = start_pending_group(store, session)
    store.resolve_approval(
        run.id,
        "call-current",
        ApprovalDecision.APPROVE,
        "approve-1",
        "approve-hash",
    )
    assert store.begin_effect(run.id, "call-current") is EffectStartResult.STARTED

    store.request_cancellation(run.id, "stop-1", "stop-hash")

    with store.connection() as connection:
        rows = connection.execute(
            "SELECT execution_state, result_json FROM tool_executions ORDER BY call_order"
        ).fetchall()
        assert rows[0][0] == ToolExecutionState.RUNNING.value
        assert rows[0][1] is None
        assert rows[1][0] == ToolExecutionState.SKIPPED.value
        assert rows[1][1] is not None
    assert [message.role for message in store.load_committed_transcript(session.id)] == ["user"]

    store.settle_tool_group(group.id, (ToolResult("call-current", "cancelled late", True),))
    assert [message.role for message in store.load_committed_transcript(session.id)] == [
        "user",
        "assistant",
        "tool",
        "tool",
    ]


def test_recovery_marks_running_unknown_skips_later_and_requires_ack(
    store: SQLiteStore, session
) -> None:
    run, _ = start_pending_group(store, session)
    store.resolve_approval(
        run.id,
        "call-current",
        ApprovalDecision.APPROVE,
        "approve-1",
        "approve-hash",
    )
    assert store.begin_effect(run.id, "call-current") is EffectStartResult.STARTED

    assert store.recover_interrupted_runs() == [run.id]
    assert store.recover_interrupted_runs() == []

    snapshot = store.load_snapshot(session.id)
    assert snapshot.active_run is None
    assert snapshot.session.requires_recovery_ack is True
    assert [message.role for message in snapshot.messages] == ["user", "assistant", "tool", "tool"]
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT execution_state FROM tool_executions ORDER BY call_order"
        ).fetchall()
        assert [row[0] for row in rows] == ["unknown", "skipped"]
        run_row = connection.execute(
            "SELECT state, stop_reason FROM runs WHERE id = ?", (run.id,)
        ).fetchone()
        assert tuple(run_row) == (RunState.INTERRUPTED.value, StopReason.SERVER_RESTART.value)

    with pytest.raises(StoreError) as raised:
        store.begin_run(session.id, "continue", {}, "start-2", "start-hash-2")
    assert raised.value.code == "RECOVERY_ACK_REQUIRED"

    first = store.acknowledge_recovery(session.id, "ack-1", "ack-hash")
    second = store.acknowledge_recovery(session.id, "ack-1", "ack-hash")
    assert first.requires_recovery_ack is False
    assert second == first
    continued = store.begin_run(session.id, "continue", {}, "start-2", "start-hash-2")
    assert continued.state is RunState.STARTING


def test_recovery_cancels_awaiting_call_without_ack_gate(store: SQLiteStore, session) -> None:
    run, _ = start_pending_group(store, session)

    assert store.recover_interrupted_runs() == [run.id]

    snapshot = store.load_snapshot(session.id)
    assert snapshot.session.requires_recovery_ack is False
    with store.connection() as connection:
        states = connection.execute(
            "SELECT execution_state, approval_status FROM tool_executions ORDER BY call_order"
        ).fetchall()
        assert [tuple(row) for row in states] == [
            ("cancelled", "cancelled"),
            ("skipped", "cancelled"),
        ]
    continued = store.begin_run(session.id, "continue", {}, "start-2", "start-hash-2")
    assert continued.state is RunState.STARTING


def test_recovery_requires_ack_when_an_active_run_has_a_committed_started_effect(
    store: SQLiteStore, session
) -> None:
    run, group = start_pending_group(store, session)
    store.resolve_approval(
        run.id,
        "call-current",
        ApprovalDecision.APPROVE,
        "approve-1",
        "approve-hash-1",
    )
    store.begin_effect(run.id, "call-current")
    store.settle_tool_group(group.id, (ToolResult("call-current", "done one", True),))
    store.resolve_approval(
        run.id,
        "call-later",
        ApprovalDecision.APPROVE,
        "approve-2",
        "approve-hash-2",
    )
    store.begin_effect(run.id, "call-later")
    store.settle_tool_group(group.id, (ToolResult("call-later", "done two", True),))

    store.recover_interrupted_runs()

    assert store.load_snapshot(session.id).session.requires_recovery_ack is True


def test_recovery_materializes_a_previously_recorded_partial_result(
    store: SQLiteStore, session
) -> None:
    run, group = start_pending_group(store, session)
    store.resolve_approval(
        run.id,
        "call-current",
        ApprovalDecision.APPROVE,
        "approve-1",
        "approve-hash-1",
    )
    store.begin_effect(run.id, "call-current")
    store.settle_tool_group(group.id, (ToolResult("call-current", "real result", True),))

    store.recover_interrupted_runs()

    history = store.load_committed_transcript(session.id)
    assert [message.role for message in history] == ["user", "assistant", "tool", "tool"]
    assert history[2].parts == (ToolResult("call-current", "real result", True),)
    assert history[3].tool_call_id == "call-later"


def test_recovery_ack_gate_and_event_roll_back_together(store: SQLiteStore, session) -> None:
    run, _ = start_pending_group(store, session)
    store.resolve_approval(
        run.id,
        "call-current",
        ApprovalDecision.APPROVE,
        "approve-1",
        "approve-hash",
    )
    store.begin_effect(run.id, "call-current")
    store.recover_interrupted_runs()
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_ack_event BEFORE INSERT ON events
            WHEN NEW.type = 'session.recovery_acknowledged'
            BEGIN SELECT RAISE(ABORT, 'injected ack failure'); END
            """
        )

    with pytest.raises(sqlite3.DatabaseError, match="injected ack failure"):
        store.acknowledge_recovery(session.id, "ack-1", "ack-hash")

    assert store.load_snapshot(session.id).session.requires_recovery_ack is True
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM client_commands WHERE client_command_id = 'ack-1'"
            ).fetchone()[0]
            == 0
        )
