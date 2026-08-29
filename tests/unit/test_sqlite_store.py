from __future__ import annotations

import sqlite3

import pytest

from coding_agent.core.errors import CommandIdConflict, StoreError
from coding_agent.core.models import (
    ApprovalDecision,
    AssistantTurn,
    EffectStartResult,
    ErrorKind,
    MessageStatus,
    ModelStopReason,
    RunState,
    RunTotals,
    StopReason,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolError,
    ToolResult,
    ToolUsePart,
    Usage,
)
from coding_agent.storage.sqlite import SQLiteStore
from coding_agent.tools import result as tool_result


@pytest.fixture
def store(tmp_path) -> SQLiteStore:
    result = SQLiteStore(tmp_path / "state.db")
    result.initialize()
    return result


@pytest.fixture
def session(store: SQLiteStore):
    return store.create_session("/tmp/workspace", "Example")


def assistant_turn_with_two_calls() -> AssistantTurn:
    return AssistantTurn(
        id="turn-1",
        parts=(
            TextPart("first"),
            ToolUsePart(ToolCall("call-1", "write_file", {"path": "a.txt"})),
            TextPart("between"),
            ToolUsePart(ToolCall("call-2", "run_command", {"command": "true"})),
        ),
        stop_reason=ModelStopReason.TOOL_USE,
        usage=Usage(input_tokens=2, output_tokens=3),
    )


def another_tool_turn() -> AssistantTurn:
    return AssistantTurn(
        id="turn-2",
        parts=(ToolUsePart(ToolCall("call-3", "read_file", {"path": "b.txt"})),),
        stop_reason=ModelStopReason.TOOL_USE,
        usage=Usage(),
    )


def mixed_tool_turn() -> AssistantTurn:
    """A read that runs automatically followed by a write that must wait for approval."""
    return AssistantTurn(
        id="turn-mixed",
        parts=(
            ToolUsePart(ToolCall("call-read", "read_file", {"path": "a.txt"})),
            ToolUsePart(ToolCall("call-write", "write_file", {"path": "b.txt"})),
        ),
        stop_reason=ModelStopReason.TOOL_USE,
        usage=Usage(),
    )


def advance_to_model_streaming(store: SQLiteStore, run_id: str) -> None:
    """Reach the only state the product ever stages a tool group from."""
    store.transition_run(run_id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
    store.transition_run(run_id, {RunState.BUILDING_CONTEXT}, RunState.MODEL_STREAMING, None, None)


def tool_envelope_result(tool_call_id: str, *, duration_ms: int) -> ToolResult:
    """Build the same envelope a local tool returns, including its measured duration."""
    return tool_result(
        tool_call_id,
        "read_file",
        ok=True,
        summary="read 1 line",
        duration_ms=duration_ms,
    )


def test_initialize_creates_v1_schema_and_configures_connections(tmp_path) -> None:
    database = tmp_path / "state.db"
    store = SQLiteStore(database, busy_timeout_ms=3210)

    store.initialize()

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == {
            "sessions",
            "runs",
            "messages",
            "model_requests",
            "tool_executions",
            "context_snapshots",
            "events",
            "client_commands",
        }
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert {row[1] for row in connection.execute("PRAGMA table_info(tool_executions)")} >= {
            "tool_call_id",
            "assistant_message_id",
            "call_order",
            "approval_status",
            "execution_state",
            "effect_started_at",
            "result_json",
        }
        assert {row[1] for row in connection.execute("PRAGMA table_info(runs)")} >= {
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "round_count",
            "retry_count",
        }
        assert {
            (row[2], row[3]) for row in connection.execute("PRAGMA foreign_key_list(runs)")
        } == {("sessions", "session_id")}

    with store.connection() as configured:
        assert configured.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert configured.execute("PRAGMA busy_timeout").fetchone()[0] == 3210


def test_create_and_list_sessions_round_trip_domain_values(store: SQLiteStore) -> None:
    first = store.create_session("/tmp/one", None)
    second = store.create_session("/tmp/two", "Second")

    assert {item.id for item in store.list_sessions()} == {first.id, second.id}
    assert first.workspace_realpath == "/tmp/one"
    assert first.requires_recovery_ack is False


def test_duplicate_command_id_returns_original_run(store: SQLiteStore, session) -> None:
    first = store.begin_run(session.id, "task", {"model": "test"}, "cmd-1", "hash-a")
    second = store.begin_run(session.id, "task", {"model": "ignored"}, "cmd-1", "hash-a")

    assert second.id == first.id
    assert [message.parts[0].text for message in store.load_committed_transcript(session.id)] == [
        "task"
    ]


def test_duplicate_command_id_with_different_payload_is_rejected(
    store: SQLiteStore, session
) -> None:
    store.begin_run(session.id, "task", {}, "cmd-1", "hash-a")

    with pytest.raises(CommandIdConflict) as raised:
        store.begin_run(session.id, "different", {}, "cmd-1", "hash-b")

    assert raised.value.code == "COMMAND_ID_CONFLICT"


def test_begin_run_rolls_back_receipt_domain_change_and_event_together(
    store: SQLiteStore, session
) -> None:
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_run_started BEFORE INSERT ON events
            WHEN NEW.type = 'run.started'
            BEGIN SELECT RAISE(ABORT, 'injected event failure'); END
            """
        )

    with pytest.raises(sqlite3.DatabaseError, match="injected event failure"):
        store.begin_run(session.id, "task", {}, "cmd-crash", "hash-crash")

    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM client_commands").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_run_started")

    retried = store.begin_run(session.id, "task", {}, "cmd-crash", "hash-crash")
    assert retried.state is RunState.STARTING


def test_pending_tool_group_is_excluded_until_results_commit_atomically(
    store: SQLiteStore, session
) -> None:
    run = store.begin_run(session.id, "change it", {}, "cmd-start", "hash-start")
    advance_to_model_streaming(store, run.id)
    pending = store.stage_tool_group(run.id, assistant_turn_with_two_calls())

    assert [message.role for message in store.load_committed_transcript(session.id)] == ["user"]

    results = (
        ToolResult("call-1", "wrote a.txt", True),
        ToolResult("call-2", "done", True),
    )
    store.settle_tool_group(pending.id, results)

    history = store.load_committed_transcript(session.id)
    assert [message.role for message in history] == ["user", "assistant", "tool", "tool"]
    assert history[1].status is MessageStatus.COMMITTED
    assert [type(part) for part in history[1].parts] == [
        TextPart,
        ToolUsePart,
        TextPart,
        ToolUsePart,
    ]
    assert [message.tool_call_id for message in history[2:]] == ["call-1", "call-2"]


def test_run_rejects_a_second_pending_tool_group(store: SQLiteStore, session) -> None:
    run = store.begin_run(session.id, "change it", {}, "cmd-start", "hash-start")
    advance_to_model_streaming(store, run.id)
    store.stage_tool_group(run.id, assistant_turn_with_two_calls())

    with pytest.raises(StoreError) as raised:
        store.stage_tool_group(run.id, another_tool_turn())

    assert raised.value.code == "PENDING_TOOL_GROUP_EXISTS"
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM messages WHERE run_id = ? AND status = 'pending_tools'",
                (run.id,),
            ).fetchone()[0]
            == 1
        )


def test_final_turn_is_rejected_until_pending_tool_group_is_settled(
    store: SQLiteStore, session
) -> None:
    run = store.begin_run(session.id, "change it", {}, "cmd-start", "hash-start")
    advance_to_model_streaming(store, run.id)
    pending = store.stage_tool_group(run.id, assistant_turn_with_two_calls())
    final = AssistantTurn(
        id="final-turn",
        parts=(TextPart("finished"),),
        stop_reason=ModelStopReason.END_TURN,
        usage=Usage(),
    )

    with pytest.raises(StoreError) as raised:
        store.commit_final_turn(run.id, final)

    assert raised.value.code == "PENDING_TOOL_GROUP_EXISTS"
    store.settle_tool_group(
        pending.id,
        (ToolResult("call-1", "one", True), ToolResult("call-2", "two", True)),
    )
    store.commit_final_turn(run.id, final)
    assert [message.role for message in store.load_committed_transcript(session.id)] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]


def test_run_cannot_become_terminal_with_a_pending_tool_group(store: SQLiteStore, session) -> None:
    run = store.begin_run(session.id, "change it", {}, "cmd-start", "hash-start")
    advance_to_model_streaming(store, run.id)
    store.stage_tool_group(run.id, assistant_turn_with_two_calls())

    with pytest.raises(StoreError) as raised:
        store.transition_run(
            run.id,
            {RunState.AWAITING_APPROVAL},
            RunState.STOPPED,
            StopReason.MAX_ROUNDS,
            None,
        )

    assert raised.value.code == "PENDING_TOOL_GROUP_EXISTS"
    assert store.load_snapshot(session.id).active_run.state is RunState.AWAITING_APPROVAL


def test_tool_group_staged_after_stop_cannot_overwrite_cancelling(
    store: SQLiteStore, session
) -> None:
    """Overwriting CANCELLING would leave a pending group no terminal transition can pass."""
    run = store.begin_run(session.id, "change it", {}, "cmd-start", "hash-start")
    advance_to_model_streaming(store, run.id)
    store.request_cancellation(run.id, "cmd-stop", "hash-stop")

    with pytest.raises(StoreError) as raised:
        store.stage_tool_group(run.id, assistant_turn_with_two_calls())

    assert raised.value.code == "RUN_CANCELLING"
    assert store.get_run(run.id).state is RunState.CANCELLING
    assert [message.role for message in store.load_committed_transcript(session.id)] == ["user"]
    cancelled = store.transition_run(
        run.id, {RunState.CANCELLING}, RunState.CANCELLED, StopReason.USER_STOP, None
    )
    assert cancelled.state is RunState.CANCELLED


def test_mixed_group_moves_the_run_state_onto_each_current_call(
    store: SQLiteStore, session
) -> None:
    """Both edges are real: an auto-executed read runs first, then a write awaits approval."""
    run = store.begin_run(session.id, "change it", {}, "cmd-start", "hash-start")
    advance_to_model_streaming(store, run.id)
    group = store.stage_tool_group(run.id, mixed_tool_turn())

    assert store.get_run(run.id).state is RunState.MODEL_STREAMING
    assert store.begin_effect(run.id, "call-read") is EffectStartResult.STARTED
    assert store.get_run(run.id).state is RunState.TOOL_RUNNING

    store.settle_tool_group(group.id, (ToolResult("call-read", "a.txt", True),))

    assert store.get_run(run.id).state is RunState.AWAITING_APPROVAL


def test_settling_a_tool_result_records_its_measured_duration(store: SQLiteStore, session) -> None:
    """A permanently NULL duration hides the per-execution timing spec 6 requires."""
    run = store.begin_run(session.id, "change it", {}, "cmd-start", "hash-start")
    advance_to_model_streaming(store, run.id)
    group = store.stage_tool_group(run.id, mixed_tool_turn())

    store.settle_tool_group(
        group.id,
        (tool_envelope_result("call-read", duration_ms=37),),
    )

    assert [tool.duration_ms for tool in store.load_snapshot(session.id).tools] == [37, None]
    assert [fact["duration_ms"] for fact in store.tool_executions_for_report(run.id)] == [37, None]


def test_snapshot_publishes_the_most_recently_finished_run(store: SQLiteStore, session) -> None:
    """Without it the stop reason vanishes the moment a run leaves the active slot."""
    first = store.begin_run(session.id, "one", {}, "cmd-one", "hash-one")
    store.transition_run(
        first.id,
        {RunState.STARTING},
        RunState.FAILED,
        StopReason.AUTH_ERROR,
        ErrorKind.AUTH_ERROR,
    )
    second = store.begin_run(session.id, "two", {}, "cmd-two", "hash-two")

    while_active = store.load_snapshot(session.id)
    store.transition_run(
        second.id, {RunState.STARTING}, RunState.STOPPED, StopReason.MAX_ROUNDS, None
    )
    once_finished = store.load_snapshot(session.id)

    assert while_active.active_run is not None
    assert while_active.active_run.id == second.id
    assert while_active.last_finished_run is not None
    assert while_active.last_finished_run.id == first.id
    assert while_active.last_finished_run.stop_reason is StopReason.AUTH_ERROR
    assert while_active.last_finished_run.error_kind is ErrorKind.AUTH_ERROR
    assert once_finished.active_run is None
    assert once_finished.last_finished_run is not None
    assert once_finished.last_finished_run.id == second.id
    assert once_finished.last_finished_run.stop_reason is StopReason.MAX_ROUNDS


def test_terminal_run_rejects_new_assistant_turns(store: SQLiteStore, session) -> None:
    run = store.begin_run(session.id, "task", {}, "cmd-start", "hash-start")
    store.transition_run(
        run.id,
        {RunState.STARTING},
        RunState.STOPPED,
        StopReason.MAX_ROUNDS,
        None,
    )
    final = AssistantTurn(
        id="final-turn",
        parts=(TextPart("too late"),),
        stop_reason=ModelStopReason.END_TURN,
        usage=Usage(),
    )

    with pytest.raises(StoreError) as stage_error:
        store.stage_tool_group(run.id, assistant_turn_with_two_calls())
    with pytest.raises(StoreError) as final_error:
        store.commit_final_turn(run.id, final)

    assert stage_error.value.code == "RUN_NOT_ACTIVE"
    assert final_error.value.code == "RUN_NOT_ACTIVE"


def test_partial_results_enable_next_call_without_exposing_an_unpaired_group(
    store: SQLiteStore, session
) -> None:
    run = store.begin_run(session.id, "change it", {}, "cmd-start", "hash-start")
    advance_to_model_streaming(store, run.id)
    pending = store.stage_tool_group(run.id, assistant_turn_with_two_calls())
    store.resolve_approval(
        run.id, "call-1", ApprovalDecision.APPROVE, "approve-1", "approve-hash-1"
    )
    assert store.begin_effect(run.id, "call-1") is EffectStartResult.STARTED

    store.settle_tool_group(pending.id, (ToolResult("call-1", "wrote a.txt", True),))

    assert [message.role for message in store.load_committed_transcript(session.id)] == ["user"]
    with store.connection() as connection:
        next_state = connection.execute(
            "SELECT execution_state FROM tool_executions WHERE tool_call_id = 'call-2'"
        ).fetchone()[0]
    assert next_state == "awaiting_approval"
    store.resolve_approval(
        run.id, "call-2", ApprovalDecision.APPROVE, "approve-2", "approve-hash-2"
    )
    assert store.begin_effect(run.id, "call-2") is EffectStartResult.STARTED
    store.settle_tool_group(pending.id, (ToolResult("call-2", "done", True),))

    history = store.load_committed_transcript(session.id)
    assert [message.role for message in history] == ["user", "assistant", "tool", "tool"]
    assert [message.parts[0].content for message in history[2:]] == ["wrote a.txt", "done"]


def test_settle_tool_group_rolls_back_results_and_commit_marker_on_event_failure(
    store: SQLiteStore, session
) -> None:
    run = store.begin_run(session.id, "change it", {}, "cmd-start", "hash-start")
    advance_to_model_streaming(store, run.id)
    pending = store.stage_tool_group(run.id, assistant_turn_with_two_calls())
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_group_settled BEFORE INSERT ON events
            WHEN NEW.type = 'tool.group_settled'
            BEGIN SELECT RAISE(ABORT, 'injected settlement failure'); END
            """
        )

    with pytest.raises(sqlite3.DatabaseError, match="injected settlement failure"):
        store.settle_tool_group(
            pending.id,
            (
                ToolResult("call-1", "failed", False, ToolError("FAILED", "failed")),
                ToolResult("call-2", "done", True),
            ),
        )

    assert [message.role for message in store.load_committed_transcript(session.id)] == ["user"]
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT execution_state, result_json FROM tool_executions ORDER BY call_order"
        ).fetchall()
        assert [(row[0], row[1]) for row in rows] == [
            ("awaiting_approval", None),
            ("queued", None),
        ]


def test_transition_run_and_event_roll_back_together(store: SQLiteStore, session) -> None:
    run = store.begin_run(session.id, "task", {}, "cmd-start", "hash-start")
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_state_event BEFORE INSERT ON events
            WHEN NEW.type = 'run.state_changed'
            BEGIN SELECT RAISE(ABORT, 'injected state failure'); END
            """
        )

    with pytest.raises(sqlite3.DatabaseError, match="injected state failure"):
        store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)

    assert store.load_snapshot(session.id).active_run.state is RunState.STARTING


def test_commit_final_turn_round_trips_as_a_committed_assistant_message(
    store: SQLiteStore, session
) -> None:
    run = store.begin_run(session.id, "task", {}, "cmd-start", "hash-start")
    turn = AssistantTurn(
        id="final-turn",
        parts=(TextPart("finished"),),
        stop_reason=ModelStopReason.END_TURN,
        usage=Usage(input_tokens=4, output_tokens=1),
    )

    store.commit_final_turn(run.id, turn)

    history = store.load_committed_transcript(session.id)
    assert [message.role for message in history] == ["user", "assistant"]
    assert history[-1].parts == (TextPart("finished"),)


def test_all_four_part_kinds_round_trip_through_parts_json_in_order(
    store: SQLiteStore, session
) -> None:
    """A thinking part must survive the parts codec like text, tool use and tool results."""
    run = store.begin_run(session.id, "task", {}, "cmd-start", "hash-start")
    store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
    store.transition_run(run.id, {RunState.BUILDING_CONTEXT}, RunState.MODEL_STREAMING, None, None)
    turn = AssistantTurn(
        id="turn-all-parts",
        parts=(
            ThinkingPart("Reasoning about the plan."),
            TextPart("I will read the file."),
            ToolUsePart(ToolCall("call-1", "read_file", {"path": "a.txt"})),
        ),
        stop_reason=ModelStopReason.TOOL_USE,
        usage=Usage(input_tokens=2, output_tokens=3),
    )

    group = store.stage_tool_group(run.id, turn)
    store.settle_tool_group(group.id, (ToolResult("call-1", "three lines", True),))

    history = store.load_committed_transcript(session.id)
    assert history[-2].parts == turn.parts
    assert history[-1].parts == (ToolResult("call-1", "three lines", True),)
    snapshot = store.load_snapshot(session.id)
    assert snapshot.messages[-2].parts == turn.parts


def test_run_totals_load_back_the_usage_and_counters_the_run_accumulated(
    store: SQLiteStore, session
) -> None:
    """Counters the runs table already sums are invisible unless the Run carries them back."""
    run = store.begin_run(session.id, "task", {}, "cmd-start", "hash-start")
    first = store.start_model_request(run.id, 1, "main", "model-a", "config-hash")
    store.finish_model_request(
        first,
        result="succeeded",
        usage=Usage(
            input_tokens=11,
            output_tokens=5,
            cache_creation_input_tokens=2,
            cache_read_input_tokens=3,
        ),
        attempt_count=2,
        network_retry_count=1,
        total_wait_ms=250,
    )
    second = store.start_model_request(run.id, 2, "main", "model-a", "config-hash")
    store.finish_model_request(
        second,
        result="succeeded",
        usage=Usage(input_tokens=7, output_tokens=1),
        attempt_count=1,
        network_retry_count=0,
        total_wait_ms=0,
    )
    compaction = store.start_model_request(run.id, 2, "compaction", "model-a", "config-hash")
    store.finish_model_request(
        compaction,
        result="succeeded",
        usage=Usage(input_tokens=4, output_tokens=2),
        attempt_count=1,
        network_retry_count=2,
        total_wait_ms=0,
    )

    active_run = store.load_snapshot(session.id).active_run

    assert active_run is not None
    assert active_run.totals == RunTotals(
        input_tokens=22,
        output_tokens=8,
        cache_creation_input_tokens=2,
        cache_read_input_tokens=3,
        round_count=2,
        retry_count=3,
    )


def test_a_run_without_model_requests_reports_zeroed_totals(store: SQLiteStore, session) -> None:
    """Zero is the stored fact for a run that has not finished a request yet."""
    store.begin_run(session.id, "task", {}, "cmd-start", "hash-start")

    active_run = store.load_snapshot(session.id).active_run

    assert active_run is not None
    assert active_run.totals == RunTotals()


def test_events_are_monotonic_per_session_and_snapshot_uses_latest_cut(
    store: SQLiteStore, session
) -> None:
    run = store.begin_run(session.id, "task", {}, "cmd-start", "hash-start")
    store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)

    events = store.events_after(session.id, 0)
    snapshot = store.load_snapshot(session.id)

    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert snapshot.snapshot_seq == events[-1].seq
    assert snapshot.active_run is not None
    assert snapshot.active_run.state is RunState.BUILDING_CONTEXT
