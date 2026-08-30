"""Transactional SQLite persistence for sessions, transcripts, and durable events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from coding_agent.core.errors import CommandIdConflict, InvalidStateTransition, StoreError
from coding_agent.core.events import validate_transition
from coding_agent.core.models import (
    ApprovalDecision,
    ApprovalRecord,
    ApprovalStatus,
    AssistantTurn,
    ContextLoad,
    ContextSnapshot,
    DurableEvent,
    EffectStartResult,
    ErrorKind,
    InterruptedRunNotice,
    Message,
    MessagePart,
    MessageStatus,
    PendingApproval,
    PendingToolGroup,
    PreparedToolCall,
    Run,
    RunContextEstimate,
    RunState,
    RunTotals,
    Session,
    SessionSnapshot,
    StopReason,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolError,
    ToolExecution,
    ToolExecutionState,
    ToolResult,
    ToolUsePart,
    Usage,
)

SCHEMA_VERSION = 3
"""The schema level ``initialize`` brings every database to, whatever it started at."""


class SQLiteStore:
    """Open short-lived, consistently configured connections to one state database."""

    def __init__(self, database: str | Path, *, busy_timeout_ms: int = 5_000) -> None:
        self._database = str(database)
        self._busy_timeout_ms = busy_timeout_ms

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms:d}")
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        schema = files("coding_agent.storage").joinpath("schema.sql").read_text(encoding="utf-8")
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(schema)
            self._upgrade_schema(connection)

    @staticmethod
    def _upgrade_schema(connection: sqlite3.Connection) -> None:
        """Bring a database created at an earlier user_version up to ``SCHEMA_VERSION``.

        ``CREATE TABLE IF NOT EXISTS`` cannot extend an existing table, so the
        presence of each versioned column is the real migration guard and user_version is
        the bookkeeping that records it.
        """
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
        session_columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if "context_json" not in run_columns:
            # v1 -> v2: the run's latest context estimate projection.
            connection.execute("ALTER TABLE runs ADD COLUMN context_json TEXT")
        if "auto_approve" not in session_columns:
            # v2 -> v3: the per-session approval mode toggle (spec 13.4).
            connection.execute(
                "ALTER TABLE sessions ADD COLUMN auto_approve INTEGER NOT NULL DEFAULT 0"
            )
        if version < SCHEMA_VERSION:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION:d}")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def create_session(self, workspace_realpath: str, title: str | None) -> Session:
        session_id = str(uuid4())
        now = _now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO sessions(id, title, workspace_realpath, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, title, workspace_realpath, now, now),
            )
            self._append_event(
                connection,
                session_id,
                None,
                "session.created",
                {"session_id": session_id},
            )
        return self.get_session(session_id)

    def list_sessions(self) -> list[Session]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC, id"
            ).fetchall()
        return [_session_from_row(row) for row in rows]

    def get_run(self, run_id: str) -> Run:
        """Return one run for runtime coordination without exposing a connection."""
        with self.connection() as connection:
            return self._get_run_from(connection, run_id)

    def begin_run(
        self,
        session_id: str,
        content: str,
        config_snapshot: Mapping[str, object],
        client_command_id: str,
        payload_hash: str,
    ) -> Run:
        with self._transaction() as connection:
            duplicate = self._existing_command(
                connection, session_id, client_command_id, payload_hash, "run.start"
            )
            if duplicate is not None:
                return self._get_run_from(connection, duplicate)

            session = self._require_session(connection, session_id)
            if session.requires_recovery_ack:
                raise StoreError(
                    "RECOVERY_ACK_REQUIRED",
                    "recovery risk must be acknowledged before starting a run",
                )
            active = connection.execute(
                f"SELECT id FROM runs WHERE state NOT IN ({_TERMINAL_PLACEHOLDERS}) LIMIT 1",
                tuple(state.value for state in _TERMINAL_STATES),
            ).fetchone()
            if active is not None:
                raise StoreError("RUN_ALREADY_ACTIVE", "another run is already active")

            run_id = str(uuid4())
            now = _now()
            self._claim_command(
                connection,
                session_id,
                client_command_id,
                "run.start",
                payload_hash,
                run_id,
                now,
            )
            connection.execute(
                """
                INSERT INTO runs(
                    id, session_id, state, config_snapshot_json, started_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    session_id,
                    RunState.STARTING.value,
                    _json(config_snapshot),
                    now,
                ),
            )
            message_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO messages(
                    id, session_id, run_id, seq, role, parts_json, status
                ) VALUES (?, ?, ?, ?, 'user', ?, ?)
                """,
                (
                    message_id,
                    session_id,
                    run_id,
                    self._next_message_seq(connection, session_id),
                    _parts_json((TextPart(content),)),
                    MessageStatus.COMMITTED.value,
                ),
            )
            event_seq = self._append_event(
                connection,
                session_id,
                run_id,
                "run.started",
                {"run_id": run_id, "message_id": message_id},
            )
            self._complete_command(connection, session_id, client_command_id, event_seq)
            connection.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            return self._get_run_from(connection, run_id)

    def load_committed_transcript(self, session_id: str) -> list[Message]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ? AND status = ?
                ORDER BY seq
                """,
                (session_id, MessageStatus.COMMITTED.value),
            ).fetchall()
        return [_message_from_row(row) for row in rows]

    def load_context_snapshot(self, session_id: str) -> ContextSnapshot | None:
        with self.connection() as connection:
            self._require_session(connection, session_id)
            row = connection.execute(
                "SELECT * FROM context_snapshots WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return _context_snapshot_from_row(row) if row is not None else None

    def replace_context_snapshot(self, snapshot: ContextSnapshot) -> None:
        with self._transaction() as connection:
            self._require_session(connection, snapshot.session_id)
            current = connection.execute(
                "SELECT model_config_json FROM context_snapshots WHERE session_id = ?",
                (snapshot.session_id,),
            ).fetchone()
            expected_version = 1
            if current is not None:
                metadata = json.loads(current["model_config_json"])
                expected_version = int(metadata.get("version", 0)) + 1
            if snapshot.version != expected_version:
                raise StoreError(
                    "SNAPSHOT_VERSION_CONFLICT",
                    f"context snapshot version must be {expected_version}",
                )

            connection.execute(
                "DELETE FROM context_snapshots WHERE session_id = ?",
                (snapshot.session_id,),
            )
            metadata = {
                "version": snapshot.version,
                "source_event_ids": snapshot.source_event_ids,
                "model": snapshot.model,
                "estimator_id": snapshot.estimator_id,
                "compaction_above_target": snapshot.compaction_above_target,
            }
            connection.execute(
                """
                INSERT INTO context_snapshots(
                    id, session_id, covered_through_message_seq, summary,
                    model_config_json, token_estimate, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    snapshot.session_id,
                    snapshot.covered_through_message_seq,
                    snapshot.summary,
                    _json(metadata),
                    snapshot.token_estimate,
                    snapshot.created_at.isoformat(),
                ),
            )

    def load_snapshot(self, session_id: str) -> SessionSnapshot:
        with self.connection() as connection:
            connection.execute("BEGIN")
            try:
                session = self._require_session(connection, session_id)
                row = connection.execute(
                    f"""
                    SELECT * FROM runs
                    WHERE session_id = ? AND state NOT IN ({_TERMINAL_PLACEHOLDERS})
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (session_id, *(state.value for state in _TERMINAL_STATES)),
                ).fetchone()
                active_run = _run_from_row(row) if row is not None else None
                finished = connection.execute(
                    f"""
                    SELECT * FROM runs
                    WHERE session_id = ? AND state IN ({_TERMINAL_PLACEHOLDERS})
                    ORDER BY finished_at DESC, started_at DESC LIMIT 1
                    """,
                    (session_id, *(state.value for state in _TERMINAL_STATES)),
                ).fetchone()
                last_finished_run = _run_from_row(finished) if finished is not None else None
                messages = tuple(
                    _message_from_row(item)
                    for item in connection.execute(
                        """
                        SELECT * FROM messages
                        WHERE session_id = ? AND status IN (?, ?) ORDER BY seq
                        """,
                        (
                            session_id,
                            MessageStatus.COMMITTED.value,
                            MessageStatus.INTERRUPTED.value,
                        ),
                    ).fetchall()
                )
                tool_rows = connection.execute(
                    """
                    SELECT tool_executions.* FROM tool_executions
                    JOIN runs ON runs.id = tool_executions.run_id
                    WHERE runs.session_id = ?
                    ORDER BY runs.started_at, tool_executions.assistant_message_id,
                             tool_executions.call_order
                    """,
                    (session_id,),
                ).fetchall()
                tools = tuple(_tool_execution_from_row(item) for item in tool_rows)
                pending_approval = _pending_approval_from_rows(connection, session_id, tool_rows)
                interrupted = connection.execute(
                    """
                    SELECT id, stop_reason FROM runs
                    WHERE session_id = ? AND state = ?
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (session_id, RunState.INTERRUPTED.value),
                ).fetchone()
                interrupted_banner = (
                    InterruptedRunNotice(
                        run_id=interrupted["id"],
                        stop_reason=StopReason(interrupted["stop_reason"]),
                        requires_recovery_ack=session.requires_recovery_ack,
                    )
                    if interrupted is not None
                    else None
                )
                snapshot_seq = connection.execute(
                    "SELECT coalesce(max(seq), 0) FROM events WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
                focus_run = active_run if active_run is not None else last_finished_run
                context_load = (
                    _context_load_from_rows(connection, session_id, focus_run, tool_rows)
                    if focus_run is not None
                    else None
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return SessionSnapshot(
            session,
            active_run,
            messages,
            snapshot_seq,
            tools,
            pending_approval,
            interrupted_banner,
            last_finished_run,
            context_load,
        )

    def stage_tool_group(self, run_id: str, turn: AssistantTurn) -> PendingToolGroup:
        if not turn.tool_calls:
            raise StoreError("TOOL_GROUP_EMPTY", "a tool group requires at least one call")
        with self._transaction() as connection:
            run = self._get_run_from(connection, run_id)
            self._require_active_run(run)
            if not _accepts_new_work(run):
                raise StoreError(
                    "RUN_CANCELLING", "a tool group cannot be staged during cancellation"
                )
            self._require_no_pending_group(connection, run.id)
            message_id = turn.id
            connection.execute(
                """
                INSERT INTO messages(
                    id, session_id, run_id, seq, role, parts_json, status
                ) VALUES (?, ?, ?, ?, 'assistant', ?, ?)
                """,
                (
                    message_id,
                    run.session_id,
                    run.id,
                    self._next_message_seq(connection, run.session_id),
                    _parts_json(turn.parts),
                    MessageStatus.PENDING_TOOLS.value,
                ),
            )
            prepared: list[PreparedToolCall] = []
            for index, call in enumerate(turn.tool_calls):
                requires_approval = call.name in {"write_file", "run_command"}
                approval_status = "pending" if requires_approval else "approved"
                state = (
                    ToolExecutionState.AWAITING_APPROVAL
                    if index == 0 and requires_approval
                    else ToolExecutionState.QUEUED
                )
                connection.execute(
                    """
                    INSERT INTO tool_executions(
                        tool_call_id, run_id, assistant_message_id, call_order, name,
                        input_json, requires_approval, approval_status, execution_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        call.id,
                        run.id,
                        message_id,
                        index,
                        call.name,
                        _json(call.input),
                        requires_approval,
                        approval_status,
                        state.value,
                    ),
                )
                prepared.append(
                    PreparedToolCall(
                        call=call,
                        requires_approval=requires_approval,
                        target=_tool_target(call),
                    )
                )
            if prepared[0].requires_approval:
                self._apply_run_state(connection, run, RunState.AWAITING_APPROVAL)
            self._append_event(
                connection,
                run.session_id,
                run.id,
                "tool.group_staged",
                {"assistant_message_id": message_id},
            )
        return PendingToolGroup(message_id, run.id, message_id, tuple(prepared))

    def settle_tool_group(self, group_id: str, results: Sequence[ToolResult]) -> None:
        with self._transaction() as connection:
            message = connection.execute(
                "SELECT * FROM messages WHERE id = ? AND status = ?",
                (group_id, MessageStatus.PENDING_TOOLS.value),
            ).fetchone()
            if message is None:
                raise StoreError("TOOL_GROUP_NOT_PENDING", "tool group is not pending")
            executions = connection.execute(
                """
                SELECT * FROM tool_executions
                WHERE assistant_message_id = ? ORDER BY call_order
                """,
                (group_id,),
            ).fetchall()
            result_by_id = {result.tool_call_id: result for result in results}
            expected_ids = [row["tool_call_id"] for row in executions]
            if (
                not results
                or len(result_by_id) != len(results)
                or not set(result_by_id).issubset(expected_ids)
            ):
                raise StoreError(
                    "TOOL_RESULT_MISMATCH",
                    "tool results must uniquely reference calls in the pending group",
                )
            for execution in executions:
                result = result_by_id.get(execution["tool_call_id"])
                if result is None:
                    continue
                encoded_result = _part_to_json_value(result)
                if execution["result_json"] is not None:
                    if execution["result_json"] != encoded_result:
                        raise StoreError(
                            "TOOL_RESULT_CONFLICT",
                            f"tool result already recorded: {result.tool_call_id}",
                        )
                    continue
                connection.execute(
                    """
                    UPDATE tool_executions
                    SET execution_state = ?, result_json = ?, duration_ms = ?
                    WHERE tool_call_id = ?
                    """,
                    (
                        _execution_state_for_result(result).value,
                        encoded_result,
                        _measured_duration_ms(result),
                        result.tool_call_id,
                    ),
                )
            executions = connection.execute(
                """
                SELECT * FROM tool_executions
                WHERE assistant_message_id = ? ORDER BY call_order
                """,
                (group_id,),
            ).fetchall()
            if any(execution["result_json"] is None for execution in executions):
                next_execution = next(
                    execution for execution in executions if execution["result_json"] is None
                )
                run = self._get_run_from(connection, message["run_id"])
                if (
                    next_execution["execution_state"] == ToolExecutionState.QUEUED.value
                    and next_execution["approval_status"] == ApprovalStatus.PENDING.value
                    and _accepts_new_work(run)
                ):
                    connection.execute(
                        """
                        UPDATE tool_executions SET execution_state = ?
                        WHERE tool_call_id = ? AND execution_state = ?
                        """,
                        (
                            ToolExecutionState.AWAITING_APPROVAL.value,
                            next_execution["tool_call_id"],
                            ToolExecutionState.QUEUED.value,
                        ),
                    )
                    self._apply_run_state(connection, run, RunState.AWAITING_APPROVAL)
                self._append_event(
                    connection,
                    message["session_id"],
                    message["run_id"],
                    "tool.result_recorded",
                    {"tool_call_ids": list(result_by_id)},
                )
                return

            next_seq = self._next_message_seq(connection, message["session_id"])
            for offset, execution in enumerate(executions):
                result = _result_from_json(execution["result_json"])
                connection.execute(
                    """
                    INSERT INTO messages(
                        id, session_id, run_id, seq, role, parts_json, status, tool_call_id
                    ) VALUES (?, ?, ?, ?, 'tool', ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        message["session_id"],
                        message["run_id"],
                        next_seq + offset,
                        _parts_json((result,)),
                        MessageStatus.COMMITTED.value,
                        result.tool_call_id,
                    ),
                )
            connection.execute(
                "UPDATE messages SET status = ? WHERE id = ?",
                (MessageStatus.COMMITTED.value, group_id),
            )
            self._append_event(
                connection,
                message["session_id"],
                message["run_id"],
                "tool.group_settled",
                {"assistant_message_id": group_id},
            )

    def commit_final_turn(self, run_id: str, turn: AssistantTurn) -> None:
        if turn.tool_calls:
            raise StoreError("FINAL_TURN_HAS_TOOLS", "a final turn cannot contain tool calls")
        with self._transaction() as connection:
            run = self._get_run_from(connection, run_id)
            self._require_active_run(run)
            self._require_no_pending_group(connection, run.id)
            connection.execute(
                """
                INSERT INTO messages(
                    id, session_id, run_id, seq, role, parts_json, status
                ) VALUES (?, ?, ?, ?, 'assistant', ?, ?)
                """,
                (
                    turn.id,
                    run.session_id,
                    run.id,
                    self._next_message_seq(connection, run.session_id),
                    _parts_json(turn.parts),
                    MessageStatus.COMMITTED.value,
                ),
            )
            self._append_event(
                connection,
                run.session_id,
                run.id,
                "assistant.turn_committed",
                {"message_id": turn.id},
            )

    def commit_final_turn_and_finish(
        self,
        run_id: str,
        turn: AssistantTurn,
        *,
        state: RunState,
        stop_reason: StopReason,
        error_kind: ErrorKind | None,
    ) -> Run:
        """Atomically commit a tool-free assistant turn and its terminal run state."""
        if turn.tool_calls:
            raise StoreError("FINAL_TURN_HAS_TOOLS", "a final turn cannot contain tool calls")
        if state not in _TERMINAL_STATES:
            raise StoreError("RUN_NOT_TERMINAL", "final turn requires a terminal run state")
        with self._transaction() as connection:
            run = self._get_run_from(connection, run_id)
            self._require_active_run(run)
            self._require_no_pending_group(connection, run.id)
            validate_transition(run.state, state)
            connection.execute(
                """
                INSERT INTO messages(
                    id, session_id, run_id, seq, role, parts_json, status
                ) VALUES (?, ?, ?, ?, 'assistant', ?, ?)
                """,
                (
                    turn.id,
                    run.session_id,
                    run.id,
                    self._next_message_seq(connection, run.session_id),
                    _parts_json(turn.parts),
                    MessageStatus.COMMITTED.value,
                ),
            )
            self._append_event(
                connection,
                run.session_id,
                run.id,
                "assistant.turn_committed",
                {"message_id": turn.id},
            )
            finished_at = _now()
            connection.execute(
                """
                UPDATE runs
                SET state = ?, stop_reason = ?, error_kind = ?, finished_at = ?
                WHERE id = ? AND state = ?
                """,
                (
                    state.value,
                    stop_reason.value,
                    error_kind.value if error_kind else None,
                    finished_at,
                    run.id,
                    run.state.value,
                ),
            )
            self._append_event(
                connection,
                run.session_id,
                run.id,
                "run.state_changed",
                {
                    "state": state.value,
                    "stop_reason": stop_reason.value,
                    "error_kind": error_kind.value if error_kind else None,
                },
            )
            return self._get_run_from(connection, run.id)

    def record_interrupted_turn(self, run_id: str, turn: AssistantTurn) -> None:
        """Retain displayable non-canonical output without exposing it to context building."""
        if not turn.parts:
            return
        with self._transaction() as connection:
            run = self._get_run_from(connection, run_id)
            self._require_active_run(run)
            connection.execute(
                """
                INSERT INTO messages(
                    id, session_id, run_id, seq, role, parts_json, status
                ) VALUES (?, ?, ?, ?, 'assistant', ?, ?)
                """,
                (
                    turn.id,
                    run.session_id,
                    run.id,
                    self._next_message_seq(connection, run.session_id),
                    _parts_json(turn.parts),
                    MessageStatus.INTERRUPTED.value,
                ),
            )
            self._append_event(
                connection,
                run.session_id,
                run.id,
                "assistant.interrupted",
                {"message_id": turn.id, "stop_reason": turn.stop_reason.value},
            )

    def start_model_request(
        self,
        run_id: str,
        round_no: int,
        kind: str,
        model: str,
        config_hash: str,
    ) -> str:
        """Persist request intent before any model I/O begins."""
        if kind not in {"main", "compaction"}:
            raise ValueError("model request kind must be main or compaction")
        request_id = str(uuid4())
        with self._transaction() as connection:
            self._require_active_run(self._get_run_from(connection, run_id))
            connection.execute(
                """
                INSERT INTO model_requests(
                    id, run_id, round_no, kind, model, config_hash, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (request_id, run_id, round_no, kind, model, config_hash, _now()),
            )
        return request_id

    def finish_model_request(
        self,
        request_id: str,
        *,
        result: str,
        usage: Usage | None,
        attempt_count: int,
        network_retry_count: int,
        total_wait_ms: int,
    ) -> None:
        """Close one request record and roll successful usage into the owning run."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT run_id, kind, finished_at FROM model_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise StoreError("MODEL_REQUEST_NOT_FOUND", "model request record not found")
            if row["finished_at"] is not None:
                raise StoreError("MODEL_REQUEST_ALREADY_FINISHED", "model request already finished")
            values = usage or Usage()
            connection.execute(
                """
                UPDATE model_requests
                SET finished_at = ?, result = ?, attempt_count = ?,
                    input_tokens = ?, output_tokens = ?,
                    cache_creation_input_tokens = ?, cache_read_input_tokens = ?,
                    usage_source = ?, network_retry_count = ?, total_wait_ms = ?
                WHERE id = ?
                """,
                (
                    _now(),
                    result,
                    attempt_count,
                    values.input_tokens,
                    values.output_tokens,
                    values.cache_creation_input_tokens,
                    values.cache_read_input_tokens,
                    "provider" if usage is not None else None,
                    network_retry_count,
                    total_wait_ms,
                    request_id,
                ),
            )
            connection.execute(
                """
                UPDATE runs
                SET input_tokens = input_tokens + coalesce(?, 0),
                    output_tokens = output_tokens + coalesce(?, 0),
                    cache_creation_input_tokens = cache_creation_input_tokens + coalesce(?, 0),
                    cache_read_input_tokens = cache_read_input_tokens + coalesce(?, 0),
                    round_count = round_count + ?,
                    retry_count = retry_count + ?
                WHERE id = ?
                """,
                (
                    values.input_tokens,
                    values.output_tokens,
                    values.cache_creation_input_tokens,
                    values.cache_read_input_tokens,
                    1 if row["kind"] == "main" else 0,
                    network_retry_count,
                    row["run_id"],
                ),
            )

    def record_context_estimate(self, run_id: str, estimate: RunContextEstimate) -> None:
        """Persist the latest context estimate as one run's read-only projection.

        The loop overwrites this after every context build, exactly like it rolls usage
        into ``totals``; a terminal run rejects the write so a finished run's projection
        stays the estimate of the view it actually used. No durable event is appended
        because the value is a projection of the run row itself (spec 13), not a state
        transition.
        """
        with self._transaction() as connection:
            run = self._get_run_from(connection, run_id)
            self._require_active_run(run)
            connection.execute(
                "UPDATE runs SET context_json = ? WHERE id = ?",
                (_json(_context_estimate_value(estimate)), run.id),
            )

    def request_cancellation(
        self,
        run_id: str,
        client_command_id: str,
        payload_hash: str,
    ) -> Run:
        with self._transaction() as connection:
            run = self._get_run_from(connection, run_id)
            duplicate = self._existing_command(
                connection,
                run.session_id,
                client_command_id,
                payload_hash,
                "run.stop",
            )
            if duplicate is not None:
                return self._get_run_from(connection, duplicate)
            if run.state in _TERMINAL_STATES:
                raise StoreError("RUN_NOT_ACTIVE", "cannot cancel a terminal run")

            now = _now()
            self._claim_command(
                connection,
                run.session_id,
                client_command_id,
                "run.stop",
                payload_hash,
                run.id,
                now,
            )
            connection.execute(
                """
                UPDATE runs
                SET state = ?, cancellation_requested_at = coalesce(cancellation_requested_at, ?)
                WHERE id = ?
                """,
                (RunState.CANCELLING.value, now, run.id),
            )
            pending_groups = connection.execute(
                """
                SELECT id FROM messages
                WHERE run_id = ? AND status = ? ORDER BY seq
                """,
                (run.id, MessageStatus.PENDING_TOOLS.value),
            ).fetchall()
            for group in pending_groups:
                executions = connection.execute(
                    """
                    SELECT * FROM tool_executions
                    WHERE assistant_message_id = ? ORDER BY call_order
                    """,
                    (group["id"],),
                ).fetchall()
                in_flight = next(
                    (
                        item
                        for item in executions
                        if item["result_json"] is None
                        and (
                            item["effect_started_at"] is not None
                            or item["execution_state"] == ToolExecutionState.RUNNING.value
                        )
                    ),
                    None,
                )
                if in_flight is None:
                    self._synthesize_group(
                        connection,
                        group["id"],
                        first_state=ToolExecutionState.CANCELLED,
                        later_state=ToolExecutionState.SKIPPED,
                    )
                else:
                    for execution in executions:
                        if (
                            execution["call_order"] > in_flight["call_order"]
                            and execution["result_json"] is None
                        ):
                            self._record_synthetic_execution(
                                connection, execution, ToolExecutionState.SKIPPED
                            )
            event_seq = self._append_event(
                connection,
                run.session_id,
                run.id,
                "run.cancellation_requested",
                {"run_id": run.id},
            )
            self._complete_command(connection, run.session_id, client_command_id, event_seq)
            return self._get_run_from(connection, run.id)

    def request_approval(self, run_id: str, prepared: PreparedToolCall) -> ApprovalRecord:
        """Audit presentation of the current frozen call before awaiting its decision."""
        tool_call_id = prepared.call.id
        with self._transaction() as connection:
            run = self._get_run_from(connection, run_id)
            self._require_active_run(run)
            if run.cancellation_requested_at is not None or run.state is RunState.CANCELLING:
                raise StoreError(
                    "RUN_CANCELLING", "approval cannot be requested during cancellation"
                )
            row = connection.execute(
                "SELECT * FROM tool_executions WHERE run_id = ? AND tool_call_id = ?",
                (run_id, tool_call_id),
            ).fetchone()
            if row is None:
                raise StoreError("TOOL_CALL_NOT_FOUND", f"tool call not found: {tool_call_id}")
            if row["approval_status"] != ApprovalStatus.PENDING.value:
                raise StoreError("APPROVAL_ALREADY_RESOLVED", "approval was already resolved")
            if row["execution_state"] != ToolExecutionState.AWAITING_APPROVAL.value:
                raise StoreError("TOOL_CALL_NOT_CURRENT", "tool call is not awaiting approval")
            if row["name"] != prepared.call.name or row["input_json"] != _json(prepared.call.input):
                raise StoreError(
                    "TOOL_CALL_CHANGED", "prepared tool call differs from its frozen record"
                )
            connection.execute(
                "UPDATE tool_executions SET baseline_sha256 = ? WHERE tool_call_id = ?",
                (prepared.baseline_sha256, tool_call_id),
            )
            self._append_event(
                connection,
                run.session_id,
                run.id,
                "approval.requested",
                {
                    "tool_call_id": tool_call_id,
                    "name": prepared.call.name,
                    "target": prepared.target,
                    "preview": prepared.preview,
                    "metadata": prepared.metadata,
                },
            )
            return self._get_approval(connection, run.id, tool_call_id)

    def resolve_approval(
        self,
        run_id: str,
        tool_call_id: str,
        decision: ApprovalDecision,
        client_command_id: str,
        payload_hash: str,
    ) -> ApprovalRecord:
        with self._transaction() as connection:
            run = self._get_run_from(connection, run_id)
            duplicate = self._existing_command(
                connection,
                run.session_id,
                client_command_id,
                payload_hash,
                "approval.resolve",
            )
            if duplicate is not None:
                return self._get_approval(connection, run.id, duplicate)
            row = connection.execute(
                """
                SELECT * FROM tool_executions
                WHERE run_id = ? AND tool_call_id = ?
                """,
                (run.id, tool_call_id),
            ).fetchone()
            if row is None:
                raise StoreError("TOOL_CALL_NOT_FOUND", f"tool call not found: {tool_call_id}")
            if run.cancellation_requested_at is not None or run.state is RunState.CANCELLING:
                raise StoreError("RUN_CANCELLING", "approval cannot be changed during cancellation")
            if row["approval_status"] != ApprovalStatus.PENDING.value:
                raise StoreError("APPROVAL_ALREADY_RESOLVED", "approval was already resolved")
            current = connection.execute(
                """
                SELECT tool_call_id FROM tool_executions
                WHERE assistant_message_id = ? AND execution_state = ?
                ORDER BY call_order LIMIT 1
                """,
                (row["assistant_message_id"], ToolExecutionState.AWAITING_APPROVAL.value),
            ).fetchone()
            if current is None or current["tool_call_id"] != tool_call_id:
                raise StoreError(
                    "TOOL_CALL_NOT_CURRENT",
                    "only the current awaiting tool call can resolve approval",
                )

            now = _now()
            self._claim_command(
                connection,
                run.session_id,
                client_command_id,
                "approval.resolve",
                payload_hash,
                tool_call_id,
                now,
            )
            status = (
                ApprovalStatus.APPROVED
                if decision is ApprovalDecision.APPROVE
                else ApprovalStatus.REJECTED
            )
            execution_state = (
                ToolExecutionState.QUEUED
                if decision is ApprovalDecision.APPROVE
                else ToolExecutionState.REJECTED
            )
            connection.execute(
                """
                UPDATE tool_executions
                SET approval_status = ?, approval_decision = ?, approval_decided_at = ?,
                    execution_state = ?
                WHERE tool_call_id = ?
                """,
                (status.value, decision.value, now, execution_state.value, tool_call_id),
            )
            if decision is ApprovalDecision.REJECT:
                rejected = _rejected_result(tool_call_id)
                connection.execute(
                    "UPDATE tool_executions SET result_json = ? WHERE tool_call_id = ?",
                    (_part_to_json_value(rejected), tool_call_id),
                )
                group_executions = connection.execute(
                    """
                    SELECT * FROM tool_executions
                    WHERE assistant_message_id = ? ORDER BY call_order
                    """,
                    (row["assistant_message_id"],),
                ).fetchall()
                states = [
                    (
                        ToolExecutionState.SKIPPED
                        if item["call_order"] > row["call_order"]
                        else ToolExecutionState(item["execution_state"])
                    )
                    for item in group_executions
                ]
                self._synthesize_group_states(connection, row["assistant_message_id"], states)
            event_seq = self._append_event(
                connection,
                run.session_id,
                run.id,
                "approval.resolved",
                {"tool_call_id": tool_call_id, "decision": decision.value},
            )
            self._complete_command(connection, run.session_id, client_command_id, event_seq)
            return self._get_approval(connection, run.id, tool_call_id)

    def acknowledge_recovery(
        self, session_id: str, client_command_id: str, payload_hash: str
    ) -> Session:
        with self._transaction() as connection:
            duplicate = self._existing_command(
                connection,
                session_id,
                client_command_id,
                payload_hash,
                "session.ack_recovery",
            )
            if duplicate is not None:
                return self._require_session(connection, duplicate)
            self._require_session(connection, session_id)
            now = _now()
            self._claim_command(
                connection,
                session_id,
                client_command_id,
                "session.ack_recovery",
                payload_hash,
                session_id,
                now,
            )
            connection.execute(
                """
                UPDATE sessions
                SET requires_recovery_ack = 0, updated_at = ?
                WHERE id = ?
                """,
                (now, session_id),
            )
            event_seq = self._append_event(
                connection,
                session_id,
                None,
                "session.recovery_acknowledged",
                {"session_id": session_id},
            )
            self._complete_command(connection, session_id, client_command_id, event_seq)
            return self._require_session(connection, session_id)

    def set_approval_mode(
        self,
        session_id: str,
        auto_approve: bool,
        client_command_id: str,
        payload_hash: str,
    ) -> Session:
        """Persist the per-session approval mode change with its audited event (spec 13.4).

        The receipt, the ``sessions.auto_approve`` column and the
        ``session.approval_mode_changed`` durable event commit in one transaction, exactly
        like ``acknowledge_recovery``; the mode applies to approval requests the loop
        makes after this commit, so a run already awaiting a decision stays interactive.
        """
        with self._transaction() as connection:
            duplicate = self._existing_command(
                connection,
                session_id,
                client_command_id,
                payload_hash,
                "session.set_approval_mode",
            )
            if duplicate is not None:
                return self._require_session(connection, duplicate)
            self._require_session(connection, session_id)
            now = _now()
            self._claim_command(
                connection,
                session_id,
                client_command_id,
                "session.set_approval_mode",
                payload_hash,
                session_id,
                now,
            )
            connection.execute(
                """
                UPDATE sessions
                SET auto_approve = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(auto_approve), now, session_id),
            )
            event_seq = self._append_event(
                connection,
                session_id,
                None,
                "session.approval_mode_changed",
                {"auto_approve": auto_approve},
            )
            self._complete_command(connection, session_id, client_command_id, event_seq)
            return self._require_session(connection, session_id)

    def completed_command_resource(
        self,
        session_id: str,
        client_command_id: str,
        payload_hash: str,
        command_type: str,
    ) -> str | None:
        """Probe whether a client command id already completed, without claiming it.

        Callers whose command spans more than one transaction — the maintenance
        compaction builds its plan before claiming the receipt — use this to answer a
        duplicate replay idempotently before any feasibility rejection can fire.
        """
        with self.connection() as connection:
            return self._existing_command(
                connection, session_id, client_command_id, payload_hash, command_type
            )

    def begin_session_compaction(
        self,
        session_id: str,
        client_command_id: str,
        payload_hash: str,
        *,
        before_estimated_tokens: int,
    ) -> Session | None:
        """Claim session.compact and emit ``compaction.started`` in one transaction.

        The maintenance flow has no run, so the compaction lifecycle lives on
        session-level durable events (spec 7.4 as amended, 14). Like ``begin_run``, the
        command receipt, the active-run rejection and the domain change commit together;
        a replayed command id returns ``None`` so the caller does not compact twice.
        """
        with self._transaction() as connection:
            duplicate = self._existing_command(
                connection,
                session_id,
                client_command_id,
                payload_hash,
                "session.compact",
            )
            if duplicate is not None:
                return None
            session = self._require_session(connection, session_id)
            if session.requires_recovery_ack:
                raise StoreError(
                    "RECOVERY_ACK_REQUIRED",
                    "recovery risk must be acknowledged before compacting",
                )
            active = connection.execute(
                f"SELECT id FROM runs WHERE state NOT IN ({_TERMINAL_PLACEHOLDERS}) LIMIT 1",
                tuple(state.value for state in _TERMINAL_STATES),
            ).fetchone()
            if active is not None:
                raise StoreError(
                    "RUN_ALREADY_ACTIVE",
                    "cannot compact while a run is active; the running loop owns compaction",
                )
            now = _now()
            self._claim_command(
                connection,
                session_id,
                client_command_id,
                "session.compact",
                payload_hash,
                session_id,
                now,
            )
            event_seq = self._append_event(
                connection,
                session_id,
                None,
                "compaction.started",
                {"before_estimated_tokens": before_estimated_tokens, "forced": True},
            )
            self._complete_command(connection, session_id, client_command_id, event_seq)
            connection.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            return session

    def finish_session_compaction(
        self,
        session_id: str,
        *,
        before_estimated_tokens: int,
        after_estimated_tokens: int,
        error_code: str | None = None,
    ) -> None:
        """Close the maintenance compaction lifecycle with ``compaction.finished``."""
        with self._transaction() as connection:
            self._require_session(connection, session_id)
            payload: dict[str, object] = {
                "before_estimated_tokens": before_estimated_tokens,
                "after_estimated_tokens": after_estimated_tokens,
                "forced": True,
            }
            if error_code is not None:
                payload["error"] = {"code": error_code}
            self._append_event(connection, session_id, None, "compaction.finished", payload)
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (_now(), session_id)
            )

    def begin_effect(self, run_id: str, tool_call_id: str) -> EffectStartResult:
        with self._transaction() as connection:
            run = self._get_run_from(connection, run_id)
            if run.cancellation_requested_at is not None or run.state is RunState.CANCELLING:
                return EffectStartResult.CANCELLING
            if run.state in _TERMINAL_STATES:
                return EffectStartResult.NOT_ACTIVE
            execution = connection.execute(
                """
                SELECT * FROM tool_executions
                WHERE run_id = ? AND tool_call_id = ?
                """,
                (run.id, tool_call_id),
            ).fetchone()
            if execution is None:
                raise StoreError("TOOL_CALL_NOT_FOUND", f"tool call not found: {tool_call_id}")
            if (
                execution["effect_started_at"] is not None
                or execution["execution_state"] != ToolExecutionState.QUEUED.value
                or execution["approval_status"] != ApprovalStatus.APPROVED.value
            ):
                return EffectStartResult.NOT_ACTIVE
            unfinished_prior = connection.execute(
                """
                SELECT 1 FROM tool_executions
                WHERE assistant_message_id = ? AND call_order < ?
                  AND execution_state NOT IN (
                      'succeeded', 'failed', 'cancelled', 'skipped', 'unknown'
                  )
                LIMIT 1
                """,
                (execution["assistant_message_id"], execution["call_order"]),
            ).fetchone()
            if unfinished_prior is not None:
                return EffectStartResult.NOT_ACTIVE

            now = _now()
            changed = connection.execute(
                """
                UPDATE tool_executions
                SET effect_started_at = ?, execution_state = ?
                WHERE tool_call_id = ? AND effect_started_at IS NULL
                  AND execution_state = ? AND approval_status = ?
                """,
                (
                    now,
                    ToolExecutionState.RUNNING.value,
                    tool_call_id,
                    ToolExecutionState.QUEUED.value,
                    ApprovalStatus.APPROVED.value,
                ),
            ).rowcount
            if changed != 1:
                return EffectStartResult.NOT_ACTIVE
            self._apply_run_state(
                connection, run, RunState.TOOL_RUNNING, require_no_cancellation=True
            )
            self._append_event(
                connection,
                run.session_id,
                run.id,
                "tool.effect_started",
                {"tool_call_id": tool_call_id},
            )
            return EffectStartResult.STARTED

    def transition_run(
        self,
        run_id: str,
        expected: Collection[RunState],
        target: RunState,
        stop_reason: StopReason | None,
        error_kind: ErrorKind | None,
    ) -> Run:
        with self._transaction() as connection:
            run = self._get_run_from(connection, run_id)
            if run.state not in expected:
                raise InvalidStateTransition(run.state, target)
            validate_transition(run.state, target)
            if target in _TERMINAL_STATES:
                self._require_no_pending_group(connection, run.id)
            finished_at = _now() if target in _TERMINAL_STATES else None
            connection.execute(
                """
                UPDATE runs
                SET state = ?, stop_reason = ?, error_kind = ?, finished_at = ?
                WHERE id = ? AND state = ?
                """,
                (
                    target.value,
                    stop_reason.value if stop_reason else None,
                    error_kind.value if error_kind else None,
                    finished_at,
                    run.id,
                    run.state.value,
                ),
            )
            self._append_event(
                connection,
                run.session_id,
                run.id,
                "run.state_changed",
                {
                    "state": target.value,
                    "stop_reason": stop_reason.value if stop_reason else None,
                    "error_kind": error_kind.value if error_kind else None,
                },
            )
            return self._get_run_from(connection, run.id)

    def schedule_model_retry(self, run_id: str, payload: Mapping[str, object]) -> Run:
        """Enter retry wait and persist the retry schedule in the same transaction."""
        with self._transaction() as connection:
            run = self._get_run_from(connection, run_id)
            validate_transition(run.state, RunState.RETRY_WAIT)
            connection.execute(
                "UPDATE runs SET state = ? WHERE id = ? AND state = ?",
                (RunState.RETRY_WAIT.value, run.id, run.state.value),
            )
            self._append_event(
                connection,
                run.session_id,
                run.id,
                "model.retry_scheduled",
                payload,
            )
            return self._get_run_from(connection, run.id)

    def record_diagnostic(
        self, run_id: str, event_type: str, payload: Mapping[str, object]
    ) -> None:
        """Append a run-scoped diagnostic event without changing run state.

        Observations such as a skipped skill are worth publishing durably, but they
        are not lifecycle transitions: the run keeps its current state and the event
        only extends the session's monotonic sequence.
        """
        if not event_type:
            raise ValueError("diagnostic event type must not be empty")
        with self._transaction() as connection:
            run = self._get_run_from(connection, run_id)
            self._append_event(connection, run.session_id, run.id, event_type, payload)

    def events_after(self, session_id: str, seq: int) -> list[DurableEvent]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE session_id = ? AND seq > ? ORDER BY seq",
                (session_id, seq),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def model_requests_for_report(self, run_id: str) -> list[dict[str, object]]:
        """Return one run's model request facts in start order for the run report."""
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT kind, round_no, result, attempt_count, network_retry_count,
                       total_wait_ms, input_tokens, output_tokens,
                       cache_creation_input_tokens, cache_read_input_tokens,
                       usage_source, started_at, finished_at
                FROM model_requests
                WHERE run_id = ?
                ORDER BY started_at, round_no, id
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def tool_executions_for_report(self, run_id: str) -> list[dict[str, object]]:
        """Return one run's tool facts as counts, hashable arguments and byte sizes only."""
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT tool_executions.name AS name,
                       tool_executions.input_json AS input_json,
                       tool_executions.execution_state AS execution_state,
                       tool_executions.effect_started_at AS effect_started_at,
                       tool_executions.duration_ms AS duration_ms,
                       tool_executions.result_json AS result_json
                FROM tool_executions
                JOIN messages ON messages.id = tool_executions.assistant_message_id
                WHERE tool_executions.run_id = ?
                ORDER BY messages.seq, tool_executions.call_order
                """,
                (run_id,),
            ).fetchall()
        facts: list[dict[str, object]] = []
        for row in rows:
            envelope = _result_facts(row["result_json"])
            facts.append(
                {
                    "name": row["name"],
                    "args_json": row["input_json"],
                    "execution_state": row["execution_state"],
                    "effect_started": row["effect_started_at"] is not None,
                    "duration_ms": row["duration_ms"],
                    **envelope,
                }
            )
        return facts

    def recover_interrupted_runs(self) -> list[str]:
        recovered: list[str] = []
        with self._transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM runs
                WHERE state NOT IN ({_TERMINAL_PLACEHOLDERS})
                ORDER BY started_at, id
                """,
                tuple(state.value for state in _TERMINAL_STATES),
            ).fetchall()
            for row in rows:
                run = _run_from_row(row)
                requires_ack = (
                    connection.execute(
                        """
                        SELECT 1 FROM tool_executions
                        WHERE run_id = ?
                          AND (effect_started_at IS NOT NULL OR execution_state = ?)
                        LIMIT 1
                        """,
                        (run.id, ToolExecutionState.UNKNOWN.value),
                    ).fetchone()
                    is not None
                )
                groups = connection.execute(
                    """
                    SELECT id FROM messages
                    WHERE run_id = ? AND status = ? ORDER BY seq
                    """,
                    (run.id, MessageStatus.PENDING_TOOLS.value),
                ).fetchall()
                for group in groups:
                    executions = connection.execute(
                        """
                        SELECT * FROM tool_executions
                        WHERE assistant_message_id = ? ORDER BY call_order
                        """,
                        (group["id"],),
                    ).fetchall()
                    first_unfinished = True
                    states: list[ToolExecutionState] = []
                    for execution in executions:
                        current = ToolExecutionState(execution["execution_state"])
                        if current in _FINAL_TOOL_STATES:
                            states.append(current)
                            if current is ToolExecutionState.UNKNOWN:
                                requires_ack = True
                            continue
                        if (
                            execution["effect_started_at"] is not None
                            or current is ToolExecutionState.RUNNING
                        ):
                            states.append(ToolExecutionState.UNKNOWN)
                            requires_ack = True
                        elif first_unfinished:
                            states.append(ToolExecutionState.CANCELLED)
                        else:
                            states.append(ToolExecutionState.SKIPPED)
                        first_unfinished = False
                    self._synthesize_group_states(connection, group["id"], states)

                now = _now()
                connection.execute(
                    """
                    UPDATE runs
                    SET state = ?, stop_reason = ?, error_kind = NULL, finished_at = ?
                    WHERE id = ?
                    """,
                    (
                        RunState.INTERRUPTED.value,
                        StopReason.SERVER_RESTART.value,
                        now,
                        run.id,
                    ),
                )
                if requires_ack:
                    connection.execute(
                        """
                        UPDATE sessions
                        SET requires_recovery_ack = 1, updated_at = ? WHERE id = ?
                        """,
                        (now, run.session_id),
                    )
                self._append_event(
                    connection,
                    run.session_id,
                    run.id,
                    "run.interrupted",
                    {
                        "stop_reason": StopReason.SERVER_RESTART.value,
                        "requires_recovery_ack": requires_ack,
                    },
                )
                recovered.append(run.id)
        return recovered

    def get_session(self, session_id: str) -> Session:
        """Return one session for runtime coordination without exposing a connection."""
        with self.connection() as connection:
            return self._require_session(connection, session_id)

    @staticmethod
    def _require_session(connection: sqlite3.Connection, session_id: str) -> Session:
        row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise StoreError("SESSION_NOT_FOUND", f"session not found: {session_id}")
        return _session_from_row(row)

    @staticmethod
    def _get_run_from(connection: sqlite3.Connection, run_id: str) -> Run:
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise StoreError("RUN_NOT_FOUND", f"run not found: {run_id}")
        return _run_from_row(row)

    @staticmethod
    def _require_active_run(run: Run) -> None:
        if run.state in _TERMINAL_STATES:
            raise StoreError("RUN_NOT_ACTIVE", "cannot append messages to a terminal run")

    def _apply_run_state(
        self,
        connection: sqlite3.Connection,
        run: Run,
        target: RunState,
        *,
        require_no_cancellation: bool = False,
    ) -> None:
        """Move one run to ``target`` only from the state this transaction observed.

        Spec 5.1 requires every lifecycle write to be an explicitly modeled edge applied
        with an expected-state condition, so a late model, approval or tool result can
        never overwrite a terminal or cancelling run. A run already in ``target`` — a
        second automatic call in the same group, for example — is left untouched because
        it is not a transition.
        """
        if run.state is target:
            return
        validate_transition(run.state, target)
        predicate = " AND cancellation_requested_at IS NULL" if require_no_cancellation else ""
        changed = connection.execute(
            f"UPDATE runs SET state = ? WHERE id = ? AND state = ?{predicate}",
            (target.value, run.id, run.state.value),
        ).rowcount
        if changed != 1:
            raise InvalidStateTransition(self._get_run_from(connection, run.id).state, target)

    @staticmethod
    def _require_no_pending_group(connection: sqlite3.Connection, run_id: str) -> None:
        pending = connection.execute(
            """
            SELECT 1 FROM messages
            WHERE run_id = ? AND status = ? LIMIT 1
            """,
            (run_id, MessageStatus.PENDING_TOOLS.value),
        ).fetchone()
        if pending is not None:
            raise StoreError(
                "PENDING_TOOL_GROUP_EXISTS",
                "the run has an unsettled tool group",
            )

    @staticmethod
    def _get_approval(
        connection: sqlite3.Connection, run_id: str, tool_call_id: str
    ) -> ApprovalRecord:
        row = connection.execute(
            """
            SELECT * FROM tool_executions
            WHERE run_id = ? AND tool_call_id = ?
            """,
            (run_id, tool_call_id),
        ).fetchone()
        if row is None:
            raise StoreError("TOOL_CALL_NOT_FOUND", f"tool call not found: {tool_call_id}")
        return ApprovalRecord(
            run_id=run_id,
            tool_call_id=tool_call_id,
            status=ApprovalStatus(row["approval_status"]),
            decision=(
                ApprovalDecision(row["approval_decision"]) if row["approval_decision"] else None
            ),
            decided_at=(
                datetime.fromisoformat(row["approval_decided_at"])
                if row["approval_decided_at"]
                else None
            ),
        )

    def _synthesize_group(
        self,
        connection: sqlite3.Connection,
        group_id: str,
        *,
        first_state: ToolExecutionState,
        later_state: ToolExecutionState,
    ) -> None:
        rows = connection.execute(
            """
            SELECT * FROM tool_executions
            WHERE assistant_message_id = ? ORDER BY call_order
            """,
            (group_id,),
        ).fetchall()
        found_unfinished = False
        states: list[ToolExecutionState] = []
        for row in rows:
            if row["result_json"] is not None:
                states.append(ToolExecutionState(row["execution_state"]))
            elif not found_unfinished:
                states.append(first_state)
                found_unfinished = True
            else:
                states.append(later_state)
        self._synthesize_group_states(connection, group_id, states)

    @staticmethod
    def _record_synthetic_execution(
        connection: sqlite3.Connection,
        execution: sqlite3.Row,
        state: ToolExecutionState,
    ) -> None:
        result = _synthetic_result(execution["tool_call_id"], state)
        connection.execute(
            """
            UPDATE tool_executions
            SET execution_state = ?, result_json = ?,
                approval_status = CASE
                    WHEN approval_status = 'pending' THEN 'cancelled'
                    ELSE approval_status
                END
            WHERE tool_call_id = ?
            """,
            (state.value, _part_to_json_value(result), execution["tool_call_id"]),
        )

    def _synthesize_group_states(
        self,
        connection: sqlite3.Connection,
        group_id: str,
        states: Sequence[ToolExecutionState],
    ) -> None:
        message = connection.execute(
            "SELECT * FROM messages WHERE id = ?",
            (group_id,),
        ).fetchone()
        if message is None:
            raise StoreError("TOOL_GROUP_NOT_FOUND", f"tool group not found: {group_id}")
        executions = connection.execute(
            """
            SELECT * FROM tool_executions
            WHERE assistant_message_id = ? ORDER BY call_order
            """,
            (group_id,),
        ).fetchall()
        if len(states) != len(executions):
            raise StoreError("TOOL_RESULT_MISMATCH", "recovery state count is invalid")
        next_seq = self._next_message_seq(connection, message["session_id"])
        for execution, state in zip(executions, states, strict=True):
            if execution["result_json"] is None:
                result = _synthetic_result(execution["tool_call_id"], state)
                self._record_synthetic_execution(connection, execution, state)
            else:
                result = _result_from_json(execution["result_json"])
            existing_message = connection.execute(
                "SELECT 1 FROM messages WHERE tool_call_id = ?",
                (execution["tool_call_id"],),
            ).fetchone()
            if existing_message is not None:
                continue
            connection.execute(
                """
                INSERT INTO messages(
                    id, session_id, run_id, seq, role, parts_json, status, tool_call_id
                ) VALUES (?, ?, ?, ?, 'tool', ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    message["session_id"],
                    message["run_id"],
                    next_seq,
                    _parts_json((result,)),
                    MessageStatus.COMMITTED.value,
                    execution["tool_call_id"],
                ),
            )
            next_seq += 1
        connection.execute(
            "UPDATE messages SET status = ? WHERE id = ?",
            (MessageStatus.COMMITTED.value, group_id),
        )

    @staticmethod
    def _next_message_seq(connection: sqlite3.Connection, session_id: str) -> int:
        return connection.execute(
            "SELECT coalesce(max(seq), 0) + 1 FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        session_id: str,
        run_id: str | None,
        event_type: str,
        payload: Mapping[str, object],
    ) -> int:
        seq = connection.execute(
            "SELECT coalesce(max(seq), 0) + 1 FROM events WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO events(session_id, seq, run_id, type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, seq, run_id, event_type, _json(payload), _now()),
        )
        return seq

    @staticmethod
    def _existing_command(
        connection: sqlite3.Connection,
        session_id: str,
        client_command_id: str,
        payload_hash: str,
        command_type: str,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT * FROM client_commands
            WHERE session_id = ? AND client_command_id = ?
            """,
            (session_id, client_command_id),
        ).fetchone()
        if row is None:
            return None
        if row["payload_hash"] != payload_hash or row["command_type"] != command_type:
            raise CommandIdConflict()
        if row["resource_id"] is None:
            raise StoreError("COMMAND_IN_PROGRESS", "command receipt has no resource yet")
        return str(row["resource_id"])

    @staticmethod
    def _claim_command(
        connection: sqlite3.Connection,
        session_id: str,
        command_id: str,
        command_type: str,
        payload_hash: str,
        resource_id: str,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO client_commands(
                session_id, client_command_id, command_type, payload_hash,
                status, resource_id, created_at
            ) VALUES (?, ?, ?, ?, 'processing', ?, ?)
            """,
            (session_id, command_id, command_type, payload_hash, resource_id, now),
        )

    @staticmethod
    def _complete_command(
        connection: sqlite3.Connection, session_id: str, command_id: str, event_seq: int
    ) -> None:
        connection.execute(
            """
            UPDATE client_commands
            SET status = 'completed', event_seq = ?, ack_json = ?
            WHERE session_id = ? AND client_command_id = ?
            """,
            (event_seq, _json({"event_seq": event_seq}), session_id, command_id),
        )


_TERMINAL_STATES = frozenset(
    {
        RunState.COMPLETED,
        RunState.STOPPED,
        RunState.CANCELLED,
        RunState.FAILED,
        RunState.INTERRUPTED,
    }
)
_TERMINAL_PLACEHOLDERS = ", ".join("?" for _ in _TERMINAL_STATES)
_FINAL_TOOL_STATES = frozenset(
    {
        ToolExecutionState.SUCCEEDED,
        ToolExecutionState.FAILED,
        ToolExecutionState.REJECTED,
        ToolExecutionState.CANCELLED,
        ToolExecutionState.SKIPPED,
        ToolExecutionState.UNKNOWN,
    }
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _accepts_new_work(run: Run) -> bool:
    """Report whether a run may still receive newly staged work.

    A persisted Stop is the linearization point of spec 5.3: once ``CANCELLING`` is
    committed, no tool group may be staged and no further approval may be requested.
    """
    return (
        run.state not in _TERMINAL_STATES
        and run.state is not RunState.CANCELLING
        and run.cancellation_requested_at is None
    )


def _json(value: object) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value


def _session_from_row(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        title=row["title"],
        workspace_realpath=row["workspace_realpath"],
        requires_recovery_ack=bool(row["requires_recovery_ack"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        auto_approve=bool(row["auto_approve"]),
    )


def _run_from_row(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"],
        session_id=row["session_id"],
        state=RunState(row["state"]),
        stop_reason=StopReason(row["stop_reason"]) if row["stop_reason"] else None,
        error_kind=ErrorKind(row["error_kind"]) if row["error_kind"] else None,
        cancellation_requested_at=(
            datetime.fromisoformat(row["cancellation_requested_at"])
            if row["cancellation_requested_at"]
            else None
        ),
        config_snapshot=json.loads(row["config_snapshot_json"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        totals=RunTotals(
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cache_creation_input_tokens=row["cache_creation_input_tokens"],
            cache_read_input_tokens=row["cache_read_input_tokens"],
            round_count=row["round_count"],
            retry_count=row["retry_count"],
        ),
        context=(
            _context_estimate_from_json(row["context_json"])
            if row["context_json"] is not None
            else None
        ),
    )


def _context_estimate_value(estimate: RunContextEstimate) -> dict[str, int]:
    return {
        "estimated_tokens": estimate.estimated_tokens,
        "available_tokens": estimate.available_tokens,
        "window_tokens": estimate.window_tokens,
        "max_output_tokens": estimate.max_output_tokens,
        "safety_margin_tokens": estimate.safety_margin_tokens,
    }


def _context_estimate_from_json(value: str) -> RunContextEstimate:
    data = json.loads(value)
    return RunContextEstimate(
        estimated_tokens=data["estimated_tokens"],
        available_tokens=data["available_tokens"],
        window_tokens=data["window_tokens"],
        max_output_tokens=data["max_output_tokens"],
        safety_margin_tokens=data["safety_margin_tokens"],
    )


def _parts_json(parts: Sequence[MessagePart]) -> str:
    return _json([_part_to_value(part) for part in parts])


def _part_to_json_value(part: MessagePart) -> str:
    return _json(_part_to_value(part))


def _part_to_value(part: MessagePart) -> dict[str, object]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ThinkingPart):
        return {"type": "thinking", "text": part.text}
    if isinstance(part, ToolUsePart):
        return {
            "type": "tool_use",
            "id": part.call.id,
            "name": part.call.name,
            "input": dict(part.call.input),
        }
    if isinstance(part, ToolResult):
        return {
            "type": "tool_result",
            "tool_call_id": part.tool_call_id,
            "content": part.content,
            "ok": part.ok,
            "error": (
                {"code": part.error.code, "message": part.error.message} if part.error else None
            ),
            "data": dict(part.data),
            "truncated": part.truncated,
        }
    raise TypeError(f"unsupported message part: {type(part).__name__}")


def _parts_from_json(value: str) -> tuple[MessagePart, ...]:
    parts: list[MessagePart] = []
    for item in json.loads(value):
        if item["type"] == "text":
            parts.append(TextPart(item["text"]))
        elif item["type"] == "thinking":
            parts.append(ThinkingPart(item["text"]))
        elif item["type"] == "tool_use":
            parts.append(ToolUsePart(ToolCall(item["id"], item["name"], item["input"])))
        elif item["type"] == "tool_result":
            error = item["error"]
            parts.append(
                ToolResult(
                    item["tool_call_id"],
                    item["content"],
                    item["ok"],
                    ToolError(error["code"], error["message"]) if error else None,
                    item.get("data", {}),
                    item.get("truncated", False),
                )
            )
        else:
            raise ValueError(f"unknown message part type: {item['type']}")
    return tuple(parts)


def _result_from_json(value: str) -> ToolResult:
    result = _parts_from_json(f"[{value}]")[0]
    if not isinstance(result, ToolResult):
        raise ValueError("stored tool result has an invalid part type")
    return result


def _result_facts(result_json: str | None) -> dict[str, object]:
    """Extract only the numeric facts of a stored tool result, never its output text."""
    if not result_json:
        return {"output_bytes": None, "truncated": False}
    value = json.loads(result_json)
    data = value.get("data") or {}
    return {
        "output_bytes": data.get("output_bytes") if isinstance(data, dict) else None,
        "truncated": bool(value.get("truncated")),
    }


def _measured_duration_ms(result: ToolResult) -> int | None:
    """Return the duration the tool itself measured while producing this result.

    The envelope is the only place a monotonic measurement exists, so it is copied into
    the ``tool_executions`` row once, as the execution settles. Results the store or the
    loop synthesizes — rejected, cancelled, skipped or unknown — never ran, so they keep
    a NULL duration instead of a fabricated zero.
    """
    try:
        envelope = json.loads(result.content)
    except json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict):
        return None
    duration = envelope.get("duration_ms")
    return duration if type(duration) is int else None


def _message_from_row(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        session_id=row["session_id"],
        run_id=row["run_id"],
        seq=row["seq"],
        role=row["role"],
        parts=_parts_from_json(row["parts_json"]),
        status=MessageStatus(row["status"]),
        tool_call_id=row["tool_call_id"],
    )


def _context_snapshot_from_row(row: sqlite3.Row) -> ContextSnapshot:
    metadata = json.loads(row["model_config_json"])
    return ContextSnapshot(
        session_id=row["session_id"],
        covered_through_message_seq=row["covered_through_message_seq"],
        summary=row["summary"],
        created_at=datetime.fromisoformat(row["created_at"]),
        version=metadata["version"],
        source_event_ids=tuple(metadata["source_event_ids"]),
        model=metadata["model"],
        estimator_id=metadata["estimator_id"],
        token_estimate=row["token_estimate"] or 0,
        compaction_above_target=metadata["compaction_above_target"],
    )


def _event_from_row(row: sqlite3.Row) -> DurableEvent:
    return DurableEvent(
        seq=row["seq"],
        session_id=row["session_id"],
        run_id=row["run_id"],
        type=row["type"],
        payload=json.loads(row["payload_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _tool_execution_from_row(row: sqlite3.Row) -> ToolExecution:
    return ToolExecution(
        tool_call_id=row["tool_call_id"],
        run_id=row["run_id"],
        assistant_message_id=row["assistant_message_id"],
        call_order=row["call_order"],
        name=row["name"],
        input=json.loads(row["input_json"]),
        requires_approval=bool(row["requires_approval"]),
        approval_status=ApprovalStatus(row["approval_status"]),
        approval_decision=(
            ApprovalDecision(row["approval_decision"]) if row["approval_decision"] else None
        ),
        approval_decided_at=(
            datetime.fromisoformat(row["approval_decided_at"])
            if row["approval_decided_at"]
            else None
        ),
        execution_state=ToolExecutionState(row["execution_state"]),
        result=_result_from_json(row["result_json"]) if row["result_json"] else None,
        duration_ms=row["duration_ms"],
    )


def _context_load_from_rows(
    connection: sqlite3.Connection,
    session_id: str,
    focus_run: Run,
    tool_rows: Sequence[sqlite3.Row],
) -> ContextLoad:
    """Assemble the focus run's context-load projection from durable evidence only.

    The AGENTS.md path comes from the run's ``run.context_loaded`` event the loop wrote
    at run start; the skills come from that run's settled ``skill`` tool executions, so a
    skill appears here only after the model actually read it (spec 13.5), never because
    discovery indexed it.
    """
    event_row = connection.execute(
        """
        SELECT payload_json FROM events
        WHERE session_id = ? AND run_id = ? AND type = 'run.context_loaded'
        ORDER BY seq DESC LIMIT 1
        """,
        (session_id, focus_run.id),
    ).fetchone()
    agents_md_path: str | None = None
    if event_row is not None:
        value = json.loads(event_row["payload_json"]).get("agents_md_path")
        agents_md_path = value if isinstance(value, str) and value else None
    skills: list[str] = []
    for row in tool_rows:
        if (
            row["run_id"] != focus_run.id
            or row["name"] != "skill"
            or row["execution_state"] != ToolExecutionState.SUCCEEDED.value
        ):
            continue
        arguments = json.loads(row["input_json"])
        if arguments.get("mode", "read") != "read":
            continue
        name = arguments.get("name")
        if isinstance(name, str) and name and name not in skills:
            skills.append(name)
    return ContextLoad(agents_md_path=agents_md_path, skills_read=tuple(skills))


def _pending_approval_from_rows(
    connection: sqlite3.Connection,
    session_id: str,
    tool_rows: Sequence[sqlite3.Row],
) -> PendingApproval | None:
    current = next(
        (
            row
            for row in tool_rows
            if row["execution_state"] == ToolExecutionState.AWAITING_APPROVAL.value
            and row["approval_status"] == ApprovalStatus.PENDING.value
        ),
        None,
    )
    if current is None:
        return None
    event_rows = connection.execute(
        """
        SELECT payload_json FROM events
        WHERE session_id = ? AND type = 'approval.requested'
        ORDER BY seq DESC
        """,
        (session_id,),
    ).fetchall()
    payload = next(
        (
            parsed
            for row in event_rows
            if (parsed := json.loads(row["payload_json"])).get("tool_call_id")
            == current["tool_call_id"]
        ),
        {},
    )
    return PendingApproval(
        run_id=current["run_id"],
        tool_call_id=current["tool_call_id"],
        name=current["name"],
        input=json.loads(current["input_json"]),
        target=payload.get("target"),
        preview=payload.get("preview"),
        metadata=payload.get("metadata", {}),
    )


def _tool_target(call: ToolCall) -> str | None:
    for key in ("path", "cwd"):
        value = call.input.get(key)
        if isinstance(value, str):
            return value
    return None


def _synthetic_result(tool_call_id: str, state: ToolExecutionState) -> ToolResult:
    details = {
        ToolExecutionState.UNKNOWN: (
            "EXECUTION_UNKNOWN",
            "execution state is unknown after server restart; inspect the workspace and "
            "running processes. P0 cannot guarantee cleanup of descendants that detached "
            "from the original process group",
        ),
        ToolExecutionState.CANCELLED: (
            "TOOL_CANCELLED",
            "tool execution was cancelled before its effect started",
        ),
        ToolExecutionState.SKIPPED: (
            "TOOL_SKIPPED",
            "tool execution was skipped because an earlier call did not complete",
        ),
        ToolExecutionState.REJECTED: (
            "TOOL_REJECTED",
            "tool execution was rejected by the user",
        ),
        ToolExecutionState.FAILED: ("TOOL_FAILED", "tool execution failed"),
        ToolExecutionState.SUCCEEDED: ("TOOL_RESULT_MISSING", "tool result was unavailable"),
    }
    code, message = details[state]
    return ToolResult(tool_call_id, message, False, ToolError(code, message))


def _rejected_result(tool_call_id: str) -> ToolResult:
    message = "tool execution was rejected by the user"
    return ToolResult(tool_call_id, message, False, ToolError("TOOL_REJECTED", message))


def _execution_state_for_result(result: ToolResult) -> ToolExecutionState:
    if result.ok:
        return ToolExecutionState.SUCCEEDED
    if result.error is None:
        return ToolExecutionState.FAILED
    if result.error.code in {"COMMAND_CANCELLED", "TOOL_CANCELLED"}:
        return ToolExecutionState.CANCELLED
    if result.error.code == "TOOL_SKIPPED":
        return ToolExecutionState.SKIPPED
    if result.error.code == "TOOL_REJECTED":
        return ToolExecutionState.REJECTED
    if result.error.code == "EXECUTION_UNKNOWN":
        return ToolExecutionState.UNKNOWN
    return ToolExecutionState.FAILED
