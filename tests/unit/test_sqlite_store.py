from __future__ import annotations

import json
import sqlite3

import pytest

from coding_agent.core.errors import CommandIdConflict, StoreError
from coding_agent.core.models import (
    ApprovalDecision,
    AssistantTurn,
    ContextLoad,
    EffectStartResult,
    ErrorKind,
    MessageStatus,
    ModelStopReason,
    RunContextEstimate,
    RunState,
    RunTotals,
    SessionTotals,
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


def test_initialize_creates_the_current_schema_and_configures_connections(tmp_path) -> None:
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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert {row[1] for row in connection.execute("PRAGMA table_info(sessions)")} >= {
            "requires_recovery_ack",
            "auto_approve",
        }
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
            "context_json",
        }
        assert {
            (row[2], row[3]) for row in connection.execute("PRAGMA foreign_key_list(runs)")
        } == {("sessions", "session_id")}

    with store.connection() as configured:
        assert configured.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert configured.execute("PRAGMA busy_timeout").fetchone()[0] == 3210


def test_initialize_upgrades_a_v1_database_keeping_its_runs(tmp_path) -> None:
    """A database created before user_version 3 must gain the new columns, not lose data."""
    database = tmp_path / "state.db"
    store = SQLiteStore(database)
    store.initialize()
    session = store.create_session("/tmp/workspace", "Legacy")
    legacy_run = store.begin_run(session.id, "kept task", {}, "legacy-cmd", "legacy-hash")
    with store.connection() as connection:
        connection.execute("ALTER TABLE runs DROP COLUMN context_json")
        connection.execute("ALTER TABLE sessions DROP COLUMN auto_approve")
        connection.execute("PRAGMA user_version = 1")

    store.initialize()

    with sqlite3.connect(database) as connection:
        assert "context_json" in {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
        assert "auto_approve" in {
            row[1] for row in connection.execute("PRAGMA table_info(sessions)")
        }
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    assert store.get_run(legacy_run.id).context is None
    estimate = _context_estimate(estimated_tokens=4321)
    store.record_context_estimate(legacy_run.id, estimate)
    assert store.get_run(legacy_run.id).context == estimate


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


def test_context_estimate_round_trips_and_the_latest_build_wins(
    store: SQLiteStore, session
) -> None:
    """The run's read-only context projection persists the latest build's estimate."""
    run = store.begin_run(session.id, "task", {}, "cmd-start", "hash-start")

    assert store.get_run(run.id).context is None
    assert store.load_snapshot(session.id).active_run is not None
    assert store.load_snapshot(session.id).active_run.context is None

    first = _context_estimate(estimated_tokens=1_200)
    store.record_context_estimate(run.id, first)
    second = _context_estimate(estimated_tokens=2_500)

    store.record_context_estimate(run.id, second)

    assert store.get_run(run.id).context == second
    snapshot = store.load_snapshot(session.id)
    assert snapshot.active_run is not None
    assert snapshot.active_run.context == second
    with store.connection() as connection:
        stored = json.loads(
            connection.execute("SELECT context_json FROM runs WHERE id = ?", (run.id,)).fetchone()[
                0
            ]
        )
    assert stored == {
        "estimated_tokens": 2_500,
        "available_tokens": 53_760,
        "window_tokens": 64_000,
        "max_output_tokens": 8_192,
        "safety_margin_tokens": 2_048,
    }
    assert first != second


def test_context_estimate_requires_an_active_run(store: SQLiteStore, session) -> None:
    """A terminal run's projection is frozen; late estimates must not rewrite history."""
    run = store.begin_run(session.id, "task", {}, "cmd-start", "hash-start")
    store.transition_run(run.id, {RunState.STARTING}, RunState.STOPPED, StopReason.MAX_ROUNDS, None)

    with pytest.raises(StoreError) as raised:
        store.record_context_estimate(run.id, _context_estimate(estimated_tokens=1))

    assert raised.value.code == "RUN_NOT_ACTIVE"
    assert store.get_run(run.id).context is None


def _context_estimate(*, estimated_tokens: int) -> RunContextEstimate:
    return RunContextEstimate(
        estimated_tokens=estimated_tokens,
        available_tokens=53_760,
        window_tokens=64_000,
        max_output_tokens=8_192,
        safety_margin_tokens=2_048,
    )


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


def test_record_diagnostic_appends_a_run_scoped_event_without_state_changes(
    store: SQLiteStore, session
) -> None:
    """A skipped skill is an observation, not a lifecycle change: state must not move."""
    run = store.begin_run(session.id, "task", {}, "cmd-start", "hash-start")
    store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
    before = store.events_after(session.id, 0)

    store.record_diagnostic(
        run.id,
        "skill.invalid",
        {"skill": ".agents/skills/broken", "code": "MISSING_DESCRIPTION", "message": "nope"},
    )

    events = store.events_after(session.id, 0)
    diagnostic = events[-1]
    assert len(events) == len(before) + 1
    assert diagnostic.type == "skill.invalid"
    assert diagnostic.run_id == run.id
    assert diagnostic.payload == {
        "skill": ".agents/skills/broken",
        "code": "MISSING_DESCRIPTION",
        "message": "nope",
    }
    assert store.get_run(run.id).state is RunState.BUILDING_CONTEXT


def test_begin_session_compaction_claims_receipt_and_emits_started_atomically(
    store: SQLiteStore, session
) -> None:
    """session.compact owes the same one-transaction receipt, check and event as run.start."""
    initiated = store.begin_session_compaction(
        session.id, "compact-1", "compact-hash-1", before_estimated_tokens=4_200
    )

    assert initiated is not None
    assert initiated.id == session.id
    events = store.events_after(session.id, 0)
    started = events[-1]
    assert started.type == "compaction.started"
    assert started.run_id is None
    assert started.payload == {"before_estimated_tokens": 4_200, "forced": True}
    with store.connection() as connection:
        receipt = connection.execute(
            """
            SELECT command_type, status, resource_id, event_seq
            FROM client_commands WHERE session_id = ? AND client_command_id = ?
            """,
            (session.id, "compact-1"),
        ).fetchone()
    assert tuple(receipt) == ("session.compact", "completed", session.id, started.seq)


def test_duplicate_session_compact_replays_without_a_second_event(
    store: SQLiteStore, session
) -> None:
    """Retrying the same command id must not compact twice."""
    store.begin_session_compaction(
        session.id, "compact-1", "compact-hash-1", before_estimated_tokens=4_200
    )
    before = store.events_after(session.id, 0)

    replay = store.begin_session_compaction(
        session.id, "compact-1", "compact-hash-1", before_estimated_tokens=9_999
    )

    assert replay is None
    assert store.events_after(session.id, 0) == before


def test_session_compact_conflicting_payload_is_rejected(store: SQLiteStore, session) -> None:
    store.begin_session_compaction(
        session.id, "compact-1", "compact-hash-1", before_estimated_tokens=4_200
    )

    with pytest.raises(CommandIdConflict) as raised:
        store.begin_session_compaction(
            session.id, "compact-1", "different-hash", before_estimated_tokens=4_200
        )

    assert raised.value.code == "COMMAND_ID_CONFLICT"


def test_session_compact_is_rejected_while_any_run_is_active(store: SQLiteStore, session) -> None:
    """The running loop owns compaction; a maintenance compact must not race it."""
    store.begin_run(session.id, "task", {}, "cmd-start", "hash-start")

    with pytest.raises(StoreError) as raised:
        store.begin_session_compaction(
            session.id, "compact-1", "compact-hash-1", before_estimated_tokens=4_200
        )

    assert raised.value.code == "RUN_ALREADY_ACTIVE"
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM client_commands WHERE command_type = 'session.compact'"
            ).fetchone()[0]
            == 0
        )
    assert all(event.type != "compaction.started" for event in store.events_after(session.id, 0))


def test_finish_session_compaction_appends_the_finished_event(store: SQLiteStore, session) -> None:
    store.begin_session_compaction(
        session.id, "compact-1", "compact-hash-1", before_estimated_tokens=4_200
    )

    store.finish_session_compaction(
        session.id,
        before_estimated_tokens=4_200,
        after_estimated_tokens=1_500,
    )

    finished = store.events_after(session.id, 0)[-1]
    assert finished.type == "compaction.finished"
    assert finished.run_id is None
    assert finished.payload == {
        "before_estimated_tokens": 4_200,
        "after_estimated_tokens": 1_500,
        "forced": True,
    }


def test_finish_session_compaction_reports_a_failure_without_changing_the_shape(
    store: SQLiteStore, session
) -> None:
    store.begin_session_compaction(
        session.id, "compact-1", "compact-hash-1", before_estimated_tokens=4_200
    )

    store.finish_session_compaction(
        session.id,
        before_estimated_tokens=4_200,
        after_estimated_tokens=4_200,
        error_code="MODEL_API_ERROR",
    )

    finished = store.events_after(session.id, 0)[-1]
    assert finished.payload == {
        "before_estimated_tokens": 4_200,
        "after_estimated_tokens": 4_200,
        "forced": True,
        "error": {"code": "MODEL_API_ERROR"},
    }


def test_set_approval_mode_round_trips_with_its_audited_event(store: SQLiteStore, session) -> None:
    """The per-session mode (spec 13.4) persists with a durable, auditable event."""
    assert session.auto_approve is False

    toggled = store.set_approval_mode(session.id, True, "mode-on", "mode-on-hash")

    assert toggled.auto_approve is True
    assert store.get_session(session.id).auto_approve is True
    events = store.events_after(session.id, 0)
    mode_events = [event for event in events if event.type == "session.approval_mode_changed"]
    assert len(mode_events) == 1
    assert mode_events[0].payload == {"auto_approve": True}
    assert mode_events[0].run_id is None

    reverted = store.set_approval_mode(session.id, False, "mode-off", "mode-off-hash")
    assert reverted.auto_approve is False
    assert store.get_session(session.id).auto_approve is False

    # A replayed command id is idempotent: it re-answers with the session and never
    # appends a second mode event, exactly like the other session-level commands.
    replayed = store.set_approval_mode(session.id, True, "mode-on", "mode-on-hash")
    assert replayed.id == session.id
    assert (
        len(
            [
                event
                for event in store.events_after(session.id, 0)
                if event.type == "session.approval_mode_changed"
            ]
        )
        == 2
    )

    with pytest.raises(CommandIdConflict):
        store.set_approval_mode(session.id, False, "mode-on", "mode-other-hash")


def test_new_sessions_default_to_interactive_approval(store: SQLiteStore) -> None:
    """Interactive approval is the default; only an explicit toggle widens it."""
    assert store.create_session("/tmp/other", "Fresh").auto_approve is False


def _seed_completed_run(store: SQLiteStore, session_id: str, *, command_id: str) -> str:
    run = store.begin_run(session_id, "task", {}, command_id, f"{command_id}-hash")
    store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
    store.transition_run(run.id, {RunState.BUILDING_CONTEXT}, RunState.MODEL_STREAMING, None, None)
    return run.id


def test_snapshot_projects_the_session_context_load(store: SQLiteStore, session) -> None:
    """AGENTS.md persists from any run that read it; skills union across runs."""
    first_run = _seed_completed_run(store, session.id, command_id="ctx-load-first")
    store.record_diagnostic(
        first_run,
        "run.context_loaded",
        {"agents_md_path": "AGENTS.md", "skills_discovered": ["git-helper", "unused"]},
    )
    skill_turn = AssistantTurn(
        id="turn-skill-first",
        parts=(ToolUsePart(ToolCall("call-skill-first", "skill", {"name": "git-helper"})),),
        stop_reason=ModelStopReason.TOOL_USE,
        usage=Usage(),
    )
    store.stage_tool_group(first_run, skill_turn)
    store.settle_tool_group(
        "turn-skill-first",
        (tool_result("call-skill-first", "skill", ok=True, summary="loaded"),),
    )
    store.commit_final_turn(
        first_run,
        AssistantTurn("turn-final-first", (TextPart("done"),), ModelStopReason.END_TURN, Usage()),
    )
    store.transition_run(
        first_run, {RunState.MODEL_STREAMING}, RunState.COMPLETED, StopReason.COMPLETED, None
    )

    # A second run reads another skill but scans a workspace whose AGENTS.md is
    # gone: neither fact may wipe the first run's contributions.
    second_run = _seed_completed_run(store, session.id, command_id="ctx-load-second")
    store.record_diagnostic(
        second_run,
        "run.context_loaded",
        {"agents_md_path": None, "skills_discovered": []},
    )
    second_skill_turn = AssistantTurn(
        id="turn-skill-second",
        parts=(ToolUsePart(ToolCall("call-skill-second", "skill", {"name": "web-evolve"})),),
        stop_reason=ModelStopReason.TOOL_USE,
        usage=Usage(),
    )
    store.stage_tool_group(second_run, second_skill_turn)
    store.settle_tool_group(
        "turn-skill-second",
        (tool_result("call-skill-second", "skill", ok=True, summary="loaded"),),
    )
    store.commit_final_turn(
        second_run,
        AssistantTurn(
            "turn-final-second", (TextPart("done"),), ModelStopReason.END_TURN, Usage()
        ),
    )
    store.transition_run(
        second_run, {RunState.MODEL_STREAMING}, RunState.COMPLETED, StopReason.COMPLETED, None
    )

    snapshot = store.load_snapshot(session.id)

    assert snapshot.active_run is None
    assert snapshot.last_finished_run is not None
    # The AGENTS.md path the first run read survives the later run that found
    # none, and the skills of both runs accumulate — while the discovered but
    # never-read skill stays out.
    assert snapshot.context_load == ContextLoad(
        agents_md_path="AGENTS.md", skills_read=("git-helper", "web-evolve")
    )


def test_snapshot_context_load_without_a_context_loaded_event(store: SQLiteStore, session) -> None:
    """A run that predates the event still yields a projection, just path-less."""
    run_id = _seed_completed_run(store, session.id, command_id="ctx-legacy-start")
    store.transition_run(
        run_id, {RunState.MODEL_STREAMING}, RunState.COMPLETED, StopReason.COMPLETED, None
    )

    snapshot = store.load_snapshot(session.id)

    assert snapshot.context_load == ContextLoad(agents_md_path=None, skills_read=())


def test_snapshot_context_load_is_null_without_any_run(store: SQLiteStore, session) -> None:
    assert store.load_snapshot(session.id).context_load is None


def test_session_totals_accumulate_runs_rounds_retries_and_tokens(
    store: SQLiteStore, session
) -> None:
    """The rail's session-scope counters sum every run of the session, retries included."""
    first = _seed_completed_run(store, session.id, command_id="totals-first")
    first_request = store.start_model_request(first, 1, "main", "model-a", "config-hash")
    store.finish_model_request(
        first_request,
        result="succeeded",
        usage=Usage(input_tokens=11, output_tokens=5),
        attempt_count=3,
        network_retry_count=2,
        total_wait_ms=0,
    )
    store.transition_run(
        first, {RunState.MODEL_STREAMING}, RunState.COMPLETED, StopReason.COMPLETED, None
    )

    # A second, still-active run keeps accumulating into the same session sums.
    second = _seed_completed_run(store, session.id, command_id="totals-second")
    second_request = store.start_model_request(second, 1, "main", "model-a", "config-hash")
    store.finish_model_request(
        second_request,
        result="succeeded",
        usage=Usage(input_tokens=7, output_tokens=1),
        attempt_count=1,
        network_retry_count=0,
        total_wait_ms=0,
    )

    snapshot = store.load_snapshot(session.id)

    assert snapshot.active_run is not None
    assert snapshot.session_totals == SessionTotals(
        run_count=2,
        round_count=2,
        retry_count=2,
        input_tokens=18,
        output_tokens=6,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


def test_session_totals_stay_null_before_the_first_run(store: SQLiteStore, session) -> None:
    assert store.load_snapshot(session.id).session_totals is None


def test_interrupted_banner_names_the_restart_interrupted_run(
    store: SQLiteStore, session
) -> None:
    run = store.begin_run(session.id, "task", {}, "cmd-start", "hash-start")
    advance_to_model_streaming(store, run.id)
    assert store.recover_interrupted_runs() == [run.id]

    snapshot = store.load_snapshot(session.id)
    assert snapshot.active_run is None
    assert snapshot.interrupted_banner is not None
    assert snapshot.interrupted_banner.run_id == run.id
    assert snapshot.interrupted_banner.stop_reason is StopReason.SERVER_RESTART


def test_interrupted_banner_retires_once_a_new_run_starts(
    store: SQLiteStore, session
) -> None:
    run = store.begin_run(session.id, "task", {}, "cmd-start", "hash-start")
    advance_to_model_streaming(store, run.id)
    assert store.recover_interrupted_runs() == [run.id]
    assert store.load_snapshot(session.id).interrupted_banner is not None

    continued = store.begin_run(session.id, "continue", {}, "cmd-2", "hash-2")
    assert continued.state is RunState.STARTING

    snapshot = store.load_snapshot(session.id)
    assert snapshot.active_run is not None
    assert snapshot.interrupted_banner is None
