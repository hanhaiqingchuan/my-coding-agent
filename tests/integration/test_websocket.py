from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from coding_agent.api.app import create_app
from coding_agent.api.websocket import WS_CLOSE_AUTH_EXPIRED, WS_CLOSE_FORBIDDEN
from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.events import RunOutcome
from coding_agent.core.models import (
    ApprovalDecision,
    AssistantTurn,
    DurableEvent,
    ModelStopReason,
    PreparedToolCall,
    RunState,
    TextPart,
    ToolCall,
    ToolUsePart,
    Usage,
)
from coding_agent.runtime.coordinator import RunCoordinator, RunMutationGate
from coding_agent.runtime.publisher import AssistantDelta, EventPublisher, ToolOutputDelta
from coding_agent.storage.sqlite import SQLiteStore

SERVER_PORT = 8123
BASE_URL = f"http://127.0.0.1:{SERVER_PORT}"
ORIGIN = BASE_URL


class _ImmediateRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(
        self,
        run_id: str,
        session_id: str,
        cancellation: CancellationToken,
    ) -> RunOutcome:
        self.calls.append(run_id)
        return RunOutcome.complete()


@dataclass(slots=True)
class _Runtime:
    app: object
    store: SQLiteStore
    publisher: EventPublisher
    runner: _ImmediateRunner


def _runtime(tmp_path: Path, *, store: SQLiteStore | None = None) -> _Runtime:
    actual_store = store or SQLiteStore(tmp_path / "state.db")
    if store is None:
        actual_store.initialize()
    publisher = EventPublisher()
    runner = _ImmediateRunner()
    coordinator = RunCoordinator(
        store=actual_store,
        mutation_gate=RunMutationGate(actual_store, publisher),
        runner=runner,
        config_snapshot={"model": "test"},
    )
    app = create_app(
        actual_store,
        coordinator,
        {"model": "test"},
        server_port=SERVER_PORT,
    )
    return _Runtime(app, actual_store, publisher, runner)


def _token(client: TestClient) -> str:
    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _connect(client: TestClient, token: str, *, headers: dict[str, str] | None = None):
    actual_headers = {"Origin": ORIGIN, "Host": f"127.0.0.1:{SERVER_PORT}"}
    if headers is not None:
        actual_headers.update(headers)
    return client.websocket_connect(
        "/api/ws",
        subprotocols=["coding-agent", token],
        headers=actual_headers,
    )


def _subscribe(websocket, session_id: str, command_id: str = "subscribe-1") -> dict:
    websocket.send_json(
        {
            "type": "session.subscribe",
            "client_command_id": command_id,
            "session_id": session_id,
            "payload": {},
        }
    )
    response = websocket.receive_json()
    assert response["type"] == "snapshot"
    assert response["client_command_id"] == command_id
    return response


def _receive_command_result(websocket, command_id: str) -> tuple[dict, list[dict]]:
    preceding: list[dict] = []
    while True:
        message = websocket.receive_json()
        if message.get("client_command_id") == command_id:
            return message, preceding
        preceding.append(message)


def _start_command(session_id: str, command_id: str, content: str = "Fix the test") -> dict:
    return {
        "type": "run.start",
        "client_command_id": command_id,
        "session_id": session_id,
        "payload": {"content": content},
    }


def _pending_approval(store: SQLiteStore, session_id: str):
    run = store.begin_run(session_id, "change file", {}, "seed-start", "seed-start-hash")
    call = ToolCall(
        "call-1",
        "write_file",
        {"operation": "write", "path": "demo.txt", "content": "hello\n"},
    )
    turn = AssistantTurn(
        "assistant-1",
        (TextPart("Changing it."), ToolUsePart(call)),
        ModelStopReason.TOOL_USE,
        Usage(),
    )
    store.stage_tool_group(run.id, turn)
    prepared = PreparedToolCall(call, True, target="demo.txt", preview="+hello")
    store.request_approval(run.id, prepared)
    return run, prepared


def test_subscribe_returns_atomic_snapshot_then_only_newer_durable_events(tmp_path: Path) -> None:
    """Registering after releasing the snapshot cut could lose an event committed in between."""

    class BlockingSnapshotStore(SQLiteStore):
        def __init__(self, database: Path) -> None:
            super().__init__(database)
            self.block_session: str | None = None
            self.snapshot_read = threading.Event()
            self.release = threading.Event()

        def load_snapshot(self, session_id: str):
            snapshot = super().load_snapshot(session_id)
            if session_id == self.block_session:
                self.block_session = None
                self.snapshot_read.set()
                assert self.release.wait(timeout=1)
            return snapshot

    store = BlockingSnapshotStore(tmp_path / "state.db")
    store.initialize()
    runtime = _runtime(tmp_path, store=store)
    session = store.create_session(str(tmp_path), "race")
    run = store.begin_run(session.id, "task", {}, "start", "start-hash")
    store.block_session = session.id

    with TestClient(runtime.app, base_url=BASE_URL) as client:
        token = _token(client)
        with _connect(client, token) as websocket:
            websocket.send_json(
                {
                    "type": "session.subscribe",
                    "client_command_id": "subscribe-race",
                    "session_id": session.id,
                    "payload": {},
                }
            )
            assert store.snapshot_read.wait(timeout=1)
            previous = store.events_after(session.id, 0)[-1].seq
            store.transition_run(
                run.id,
                {RunState.STARTING},
                RunState.BUILDING_CONTEXT,
                None,
                None,
            )
            committed = store.events_after(session.id, previous)[0]
            store.release.set()
            snapshot_message = websocket.receive_json()
            assert snapshot_message["snapshot"]["snapshot_seq"] == previous

            assert client.portal is not None
            client.portal.call(runtime.publisher.publish_committed, committed)
            delivered = websocket.receive_json()
            assert delivered["type"] == "durable"
            assert delivered["event"]["seq"] == committed.seq

            websocket.send_json(
                {
                    "type": "unknown.command",
                    "client_command_id": "after-race",
                    "session_id": session.id,
                    "payload": {},
                }
            )
            assert websocket.receive_json()["type"] == "command_error"


def test_duplicate_run_start_is_idempotent_and_conflicting_payload_is_rejected(
    tmp_path: Path,
) -> None:
    """Retrying one browser command must own one Run, while payload reuse must conflict."""
    runtime = _runtime(tmp_path)
    session = runtime.store.create_session(str(tmp_path), "idempotency")

    with TestClient(runtime.app, base_url=BASE_URL) as client:
        with _connect(client, _token(client)) as websocket:
            _subscribe(websocket, session.id)
            websocket.send_json(_start_command(session.id, "same-id"))
            first, first_preceding = _receive_command_result(websocket, "same-id")
            websocket.send_json(_start_command(session.id, "same-id"))
            second, second_preceding = _receive_command_result(websocket, "same-id")
            websocket.send_json(_start_command(session.id, "same-id", "Different task"))
            conflict, conflict_preceding = _receive_command_result(websocket, "same-id")

    assert first["type"] == "ack"
    assert second["type"] == "ack"
    assert second["resource_id"] == first["resource_id"]
    assert conflict["type"] == "command_error"
    assert conflict["code"] == "COMMAND_ID_CONFLICT"
    assert [item["type"] for item in first_preceding + second_preceding] == ["durable"]
    assert conflict_preceding == []
    assert runtime.runner.calls == [first["resource_id"]]
    with runtime.store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


def test_failed_start_transaction_can_be_replayed_without_a_stranded_receipt(
    tmp_path: Path,
) -> None:
    """A crash after receipt insertion must roll back both the receipt and Run mutation."""
    runtime = _runtime(tmp_path)
    session = runtime.store.create_session(str(tmp_path), "rollback")
    with runtime.store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_run_started BEFORE INSERT ON events
            WHEN NEW.type = 'run.started'
            BEGIN SELECT RAISE(ABORT, 'injected start failure'); END
            """
        )

    with TestClient(runtime.app, base_url=BASE_URL) as client:
        with _connect(client, _token(client)) as websocket:
            _subscribe(websocket, session.id)
            websocket.send_json(_start_command(session.id, "retry-after-crash"))
            failed, _ = _receive_command_result(websocket, "retry-after-crash")
            assert failed["type"] == "command_error"
            assert failed["code"] == "INTERNAL_ERROR"

            with runtime.store.connection() as connection:
                assert connection.execute("SELECT count(*) FROM client_commands").fetchone()[0] == 0
                assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
                connection.execute("DROP TRIGGER fail_run_started")

            websocket.send_json(_start_command(session.id, "retry-after-crash"))
            replayed, _ = _receive_command_result(websocket, "retry-after-crash")
            assert replayed["type"] == "ack"


def test_global_active_run_and_run_session_identity_are_enforced(tmp_path: Path) -> None:
    """A command from one Session must not mutate another Session's active Run."""
    runtime = _runtime(tmp_path)
    first_session = runtime.store.create_session(str(tmp_path), "first")
    second_session = runtime.store.create_session(str(tmp_path), "second")

    with TestClient(runtime.app, base_url=BASE_URL) as client:
        token = _token(client)
        with _connect(client, token) as first_socket:
            _subscribe(first_socket, first_session.id, "subscribe-first")
            first_socket.send_json(_start_command(first_session.id, "start-first"))
            started, _ = _receive_command_result(first_socket, "start-first")
        with _connect(client, token) as second_socket:
            _subscribe(second_socket, second_session.id, "subscribe-second")
            second_socket.send_json(_start_command(second_session.id, "start-second"))
            rejected, _ = _receive_command_result(second_socket, "start-second")
            second_socket.send_json(
                {
                    "type": "run.stop",
                    "client_command_id": "wrong-session-stop",
                    "session_id": second_session.id,
                    "payload": {"run_id": started["resource_id"]},
                }
            )
            mismatch, _ = _receive_command_result(second_socket, "wrong-session-stop")

    assert rejected["code"] == "RUN_ALREADY_ACTIVE"
    assert mismatch["code"] == "RUN_SESSION_MISMATCH"
    assert runtime.store.get_run(started["resource_id"]).state is RunState.STARTING


def test_stop_and_approval_commands_validate_resources_and_return_stable_acks(
    tmp_path: Path,
) -> None:
    """An approval must reference the current frozen call on the command's Run."""
    runtime = _runtime(tmp_path)
    session = runtime.store.create_session(str(tmp_path), "approval")
    run, prepared = _pending_approval(runtime.store, session.id)

    with TestClient(runtime.app, base_url=BASE_URL) as client:
        with _connect(client, _token(client)) as websocket:
            _subscribe(websocket, session.id)
            websocket.send_json(
                {
                    "type": "approval.resolve",
                    "client_command_id": "bad-approval",
                    "session_id": session.id,
                    "payload": {
                        "run_id": run.id,
                        "tool_call_id": "missing-call",
                        "decision": "approve",
                    },
                }
            )
            invalid, _ = _receive_command_result(websocket, "bad-approval")
            websocket.send_json(
                {
                    "type": "approval.resolve",
                    "client_command_id": "approve",
                    "session_id": session.id,
                    "payload": {
                        "run_id": run.id,
                        "tool_call_id": prepared.call.id,
                        "decision": "reject",
                    },
                }
            )
            approved, _ = _receive_command_result(websocket, "approve")
            websocket.send_json(
                {
                    "type": "run.stop",
                    "client_command_id": "stop",
                    "session_id": session.id,
                    "payload": {"run_id": run.id},
                }
            )
            stopped, _ = _receive_command_result(websocket, "stop")

    assert invalid["type"] == "command_error"
    assert invalid["code"] == "TOOL_CALL_NOT_FOUND"
    assert approved["type"] == "ack"
    assert approved["resource_id"] == prepared.call.id
    assert stopped["type"] == "ack"
    assert stopped["resource_id"] == run.id
    assert runtime.store.get_run(run.id).state is RunState.CANCELLING


def test_recovery_gate_allows_only_subscribe_and_idempotent_ack(tmp_path: Path) -> None:
    """Unknown tool side effects must be acknowledged before any new mutation."""
    runtime = _runtime(tmp_path)
    session = runtime.store.create_session(str(tmp_path), "recovery")
    interrupted, prepared = _pending_approval(runtime.store, session.id)
    runtime.store.resolve_approval(
        interrupted.id,
        prepared.call.id,
        ApprovalDecision.APPROVE,
        "seed-approve",
        "seed-approve-hash",
    )
    runtime.store.begin_effect(interrupted.id, prepared.call.id)
    runtime.store.recover_interrupted_runs()

    with TestClient(runtime.app, base_url=BASE_URL) as client:
        with _connect(client, _token(client)) as websocket:
            snapshot = _subscribe(websocket, session.id)
            assert snapshot["snapshot"]["session"]["requires_recovery_ack"] is True
            assert snapshot["snapshot"]["interrupted_banner"] == {
                "run_id": interrupted.id,
                "stop_reason": "server_restart",
                "requires_recovery_ack": True,
            }
            websocket.send_json(_start_command(session.id, "blocked-start"))
            blocked, _ = _receive_command_result(websocket, "blocked-start")
            websocket.send_json(
                {
                    "type": "session.ack_recovery",
                    "client_command_id": "ack-risk",
                    "session_id": session.id,
                    "payload": {},
                }
            )
            first_ack, _ = _receive_command_result(websocket, "ack-risk")
            websocket.send_json(
                {
                    "type": "session.ack_recovery",
                    "client_command_id": "ack-risk",
                    "session_id": session.id,
                    "payload": {},
                }
            )
            second_ack, _ = _receive_command_result(websocket, "ack-risk")
            websocket.send_json(_start_command(session.id, "after-ack"))
            started, _ = _receive_command_result(websocket, "after-ack")

    assert blocked["code"] == "RECOVERY_ACK_REQUIRED"
    assert first_ack["type"] == second_ack["type"] == "ack"
    assert first_ack["resource_id"] == second_ack["resource_id"] == session.id
    assert started["type"] == "ack"
    with runtime.store.connection() as connection:
        count = connection.execute(
            "SELECT count(*) FROM events WHERE type = 'session.recovery_acknowledged'"
        ).fetchone()[0]
    assert count == 1


def test_invalid_commands_and_unknown_sessions_return_command_errors(tmp_path: Path) -> None:
    """Permissive command parsing could execute an unintended branch or ignore unsafe fields."""
    runtime = _runtime(tmp_path)
    session = runtime.store.create_session(str(tmp_path), "invalid")

    with TestClient(runtime.app, base_url=BASE_URL) as client:
        with _connect(client, _token(client)) as websocket:
            websocket.send_json(
                {
                    "type": "session.subscribe",
                    "client_command_id": "extra-field",
                    "session_id": session.id,
                    "payload": {},
                    "unexpected": True,
                }
            )
            extra = websocket.receive_json()
            websocket.send_json(
                {
                    "type": "session.subscribe",
                    "client_command_id": "unknown-session",
                    "session_id": "00000000-0000-0000-0000-000000000000",
                    "payload": {},
                }
            )
            missing = websocket.receive_json()

    assert extra["type"] == "command_error"
    assert extra["code"] == "INVALID_COMMAND"
    assert missing["type"] == "command_error"
    assert missing["code"] == "SESSION_NOT_FOUND"


def test_websocket_rejects_bad_host_origin_and_expired_process_token(tmp_path: Path) -> None:
    """A restarted or cross-origin browser must fail before it can issue commands."""
    runtime = _runtime(tmp_path)
    with TestClient(runtime.app, base_url=BASE_URL) as client:
        token = _token(client)
        with pytest.raises(WebSocketDisconnect) as missing_token:
            with client.websocket_connect(
                "/api/ws",
                headers={"Origin": ORIGIN, "Host": f"127.0.0.1:{SERVER_PORT}"},
            ):
                pass
        with pytest.raises(WebSocketDisconnect) as malicious_origin:
            with _connect(client, token, headers={"Origin": "https://attacker.invalid"}):
                pass
        with pytest.raises(WebSocketDisconnect) as wrong_host:
            with _connect(client, token, headers={"Host": "127.0.0.1:8124"}):
                pass

    restarted = _runtime(tmp_path, store=runtime.store)
    with TestClient(restarted.app, base_url=BASE_URL) as client:
        with pytest.raises(WebSocketDisconnect) as expired:
            with _connect(client, token):
                pass
        fresh = _token(client)
        with _connect(client, fresh) as websocket:
            websocket.send_json(
                {
                    "type": "session.subscribe",
                    "client_command_id": "missing",
                    "session_id": "00000000-0000-0000-0000-000000000000",
                    "payload": {},
                }
            )
            assert websocket.receive_json()["type"] == "command_error"

    assert missing_token.value.code == WS_CLOSE_AUTH_EXPIRED
    assert expired.value.code == WS_CLOSE_AUTH_EXPIRED
    assert malicious_origin.value.code == WS_CLOSE_FORBIDDEN
    assert wrong_host.value.code == WS_CLOSE_FORBIDDEN


def test_development_origin_is_normalized_and_must_remain_loopback(tmp_path: Path) -> None:
    """A broad development-origin escape hatch would defeat the production same-origin policy."""
    runtime = _runtime(tmp_path)
    with pytest.raises(ValueError, match="loopback"):
        create_app(
            runtime.store,
            None,
            {},
            server_port=SERVER_PORT,
            development_origin="https://example.com:5173",
        )

    development_app = create_app(
        runtime.store,
        None,
        {},
        server_port=SERVER_PORT,
        development_origin="http://localhost:5173/",
    )
    with TestClient(development_app, base_url=BASE_URL) as client:
        token = _token(client)
        with client.websocket_connect(
            "/api/ws",
            subprotocols=["coding-agent", token],
            headers={
                "Origin": "http://localhost:5173",
                "Host": f"127.0.0.1:{SERVER_PORT}",
            },
        ):
            pass


def test_delta_envelopes_always_identify_run_and_draft_epoch(tmp_path: Path) -> None:
    """An epoch-less reconnect could append stale stream text to a newer draft."""
    runtime = _runtime(tmp_path)
    session = runtime.store.create_session(str(tmp_path), "deltas")

    with TestClient(runtime.app, base_url=BASE_URL) as client:
        with _connect(client, _token(client)) as websocket:
            _subscribe(websocket, session.id)
            assert client.portal is not None
            client.portal.call(
                runtime.publisher.publish_transient,
                AssistantDelta(session.id, "run-1", "epoch-1", 0, "hello"),
            )
            assistant = websocket.receive_json()
            client.portal.call(
                runtime.publisher.publish_transient,
                ToolOutputDelta(session.id, "run-1", "epoch-tool", "call-1", "line\n"),
            )
            tool = websocket.receive_json()

    assert assistant == {
        "type": "assistant.delta",
        "session_id": session.id,
        "run_id": "run-1",
        "draft_epoch": "epoch-1",
        "index": 0,
        "text": "hello",
    }
    assert tool == {
        "type": "tool.output.delta",
        "session_id": session.id,
        "run_id": "run-1",
        "draft_epoch": "epoch-tool",
        "tool_call_id": "call-1",
        "text": "line\n",
    }


def test_disconnect_unsubscribes_without_cancelling_the_active_run(tmp_path: Path) -> None:
    """A browser transport loss must not be interpreted as an explicit Stop command."""
    runtime = _runtime(tmp_path)
    session = runtime.store.create_session(str(tmp_path), "disconnect")

    with TestClient(runtime.app, base_url=BASE_URL) as client:
        with _connect(client, _token(client)) as websocket:
            _subscribe(websocket, session.id)
            websocket.send_json(_start_command(session.id, "start"))
            ack, _ = _receive_command_result(websocket, "start")

    assert runtime.store.get_run(ack["resource_id"]).state is RunState.STARTING


def test_switching_sessions_removes_the_previous_subscription(tmp_path: Path) -> None:
    """Leaving the old subscription attached could leak another workspace's events."""
    runtime = _runtime(tmp_path)
    first = runtime.store.create_session(str(tmp_path), "first")
    second = runtime.store.create_session(str(tmp_path), "second")

    with TestClient(runtime.app, base_url=BASE_URL) as client:
        with _connect(client, _token(client)) as websocket:
            _subscribe(websocket, first.id, "subscribe-first")
            _subscribe(websocket, second.id, "subscribe-second")
            assert client.portal is not None
            client.portal.call(
                runtime.publisher.publish_committed,
                DurableEvent(100, first.id, None, "first.event", {}, datetime.now(UTC)),
            )
            client.portal.call(
                runtime.publisher.publish_committed,
                DurableEvent(100, second.id, None, "second.event", {}, datetime.now(UTC)),
            )
            received = websocket.receive_json()

    assert received["type"] == "durable"
    assert received["event"]["session_id"] == second.id
    assert received["event"]["type"] == "second.event"
