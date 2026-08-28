from __future__ import annotations

import sqlite3

import pytest

from coding_agent.core.errors import CommandIdConflict
from coding_agent.core.models import (
    ApprovalDecision,
    AssistantTurn,
    EffectStartResult,
    MessageStatus,
    ModelStopReason,
    RunState,
    TextPart,
    ToolCall,
    ToolError,
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


def test_partial_results_enable_next_call_without_exposing_an_unpaired_group(
    store: SQLiteStore, session
) -> None:
    run = store.begin_run(session.id, "change it", {}, "cmd-start", "hash-start")
    pending = store.stage_tool_group(run.id, assistant_turn_with_two_calls())
    store.resolve_approval(
        run.id, "call-1", ApprovalDecision.APPROVE, "approve-1", "approve-hash-1"
    )
    assert store.begin_effect(run.id, "call-1") is EffectStartResult.STARTED

    store.settle_tool_group(pending.id, (ToolResult("call-1", "wrote a.txt", True),))

    assert [message.role for message in store.load_committed_transcript(session.id)] == ["user"]
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
