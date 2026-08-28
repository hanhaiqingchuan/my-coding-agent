from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from coding_agent.api.app import create_app
from coding_agent.api.schemas import SessionSnapshotDto
from coding_agent.core.models import (
    ApprovalDecision,
    ApprovalStatus,
    AssistantTurn,
    ModelStopReason,
    PreparedToolCall,
    RunState,
    StopReason,
    TextPart,
    ToolCall,
    ToolExecutionState,
    ToolUsePart,
    Usage,
)
from coding_agent.runtime.publisher import EventPublisher
from coding_agent.storage.sqlite import SQLiteStore

SERVER_PORT = 8123
BASE_URL = f"http://127.0.0.1:{SERVER_PORT}"
ORIGIN = BASE_URL


def _make_client(tmp_path: Path) -> tuple[TestClient, SQLiteStore, Path]:
    database = tmp_path / "state.db"
    store = SQLiteStore(database)
    store.initialize()
    app = create_app(
        store=store,
        coordinator=None,
        public_config={
            "model": "claude-test",
            "context_window": 64_000,
            "max_output_tokens": 8_192,
            "api_key": "secret-sentinel",
            "nested": {"access_token": "nested-secret"},
        },
        event_publisher=EventPublisher(),
        server_port=SERVER_PORT,
    )
    return TestClient(app, base_url=BASE_URL), store, database


def _token(client: TestClient) -> str:
    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _mutation_headers(token: str, *, origin: str = ORIGIN) -> dict[str, str]:
    return {"X-CSRF-Token": token, "Origin": origin}


def test_health_bootstrap_and_public_config_expose_only_browser_safe_values(tmp_path: Path) -> None:
    """Leaking process secrets or an unusable socket URL would break safe bootstrap."""
    client, _, _ = _make_client(tmp_path)

    assert client.get("/api/health").json() == {"status": "ok"}
    bootstrap = client.get("/api/bootstrap").json()
    assert bootstrap["csrf_token"]
    assert bootstrap["websocket_url"] == f"ws://127.0.0.1:{SERVER_PORT}/api/ws"
    assert client.get("/api/config/public").json() == {
        "model": "claude-test",
        "context_window": 64_000,
        "max_output_tokens": 8_192,
    }
    assert "secret" not in client.get("/api/config/public").text.lower()


def test_directory_listing_returns_canonical_accessible_children(tmp_path: Path) -> None:
    """Returning unchecked paths would let the browser select a file or stale symlink."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "b-dir").mkdir()
    (workspace / "a-dir").mkdir()
    (workspace / "plain.txt").write_text("not a directory", encoding="utf-8")
    client, _, _ = _make_client(tmp_path)

    response = client.get("/api/directories", params={"path": str(workspace)})

    assert response.status_code == 200
    assert response.json() == {
        "path": str(workspace.resolve()),
        "directories": [
            {"name": "a-dir", "path": str((workspace / "a-dir").resolve())},
            {"name": "b-dir", "path": str((workspace / "b-dir").resolve())},
        ],
    }


def test_directory_listing_rejects_relative_missing_and_non_directory_paths(tmp_path: Path) -> None:
    """Accepting a non-canonical directory would make Session workspace identity unstable."""
    plain_file = tmp_path / "plain.txt"
    plain_file.write_text("file", encoding="utf-8")
    client, _, _ = _make_client(tmp_path)

    for path in ("relative", str(tmp_path / "missing"), str(plain_file)):
        response = client.get("/api/directories", params={"path": path})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_DIRECTORY"


def test_create_list_and_snapshot_preserve_canonical_workspace(tmp_path: Path) -> None:
    """Re-resolving a user path later could silently move a Session to another workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = tmp_path / "workspace-alias"
    alias.symlink_to(workspace, target_is_directory=True)
    client, store, database = _make_client(tmp_path)
    token = _token(client)

    created_response = client.post(
        "/api/sessions",
        json={"workspace": str(alias), "title": "Demo"},
        headers=_mutation_headers(token),
    )

    assert created_response.status_code == 201
    created = created_response.json()
    assert created["workspace_realpath"] == str(workspace.resolve())
    assert client.get("/api/sessions").json() == [created]

    run = store.begin_run(created["id"], "Fix the test", {}, "direct-start", "direct-hash")
    snapshot = client.get(f"/api/sessions/{created['id']}/snapshot")
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["snapshot_seq"] == 2
    assert body["session"]["id"] == created["id"]
    assert body["session"]["title"] == "Demo"
    assert body["session"]["workspace_realpath"] == str(workspace.resolve())
    assert body["session"]["requires_recovery_ack"] is False
    assert body["session"]["updated_at"] >= created["updated_at"]
    assert body["active_run"]["id"] == run.id
    assert body["active_run"]["state"] == "starting"
    assert body["messages"] == [
        {
            "id": body["messages"][0]["id"],
            "session_id": created["id"],
            "run_id": run.id,
            "seq": 1,
            "role": "user",
            "parts": [{"type": "text", "text": "Fix the test"}],
            "status": "committed",
            "tool_call_id": None,
        }
    ]
    assert token.encode() not in database.read_bytes()


def test_session_creation_requires_same_origin_process_token_and_valid_directory(
    tmp_path: Path,
) -> None:
    """A cross-origin caller or stale token must not create durable local state."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client, _, _ = _make_client(tmp_path)
    token = _token(client)
    body = {"workspace": str(workspace), "title": None}

    assert client.post("/api/sessions", json=body).status_code == 403
    assert (
        client.post(
            "/api/sessions",
            json=body,
            headers=_mutation_headers("wrong-token"),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/sessions",
            json=body,
            headers=_mutation_headers(token, origin="https://attacker.invalid"),
        ).status_code
        == 403
    )
    invalid = client.post(
        "/api/sessions",
        json={"workspace": str(tmp_path / "missing"), "title": None},
        headers=_mutation_headers(token),
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "INVALID_DIRECTORY"


def test_snapshot_includes_frozen_tools_and_the_current_pending_approval(tmp_path: Path) -> None:
    """Omitting pending tools would make a refreshed approval prompt impossible to restore."""
    client, store, _ = _make_client(tmp_path)
    session = store.create_session(str(tmp_path.resolve()), "Approval")
    run = store.begin_run(session.id, "change file", {}, "start", "start-hash")
    call = ToolCall(
        "call-1",
        "write_file",
        {"operation": "write", "path": "demo.txt", "content": "hello\n"},
    )
    turn = AssistantTurn(
        "assistant-1",
        (TextPart("I will update it."), ToolUsePart(call)),
        ModelStopReason.TOOL_USE,
        Usage(input_tokens=3, output_tokens=4),
    )
    store.stage_tool_group(run.id, turn)
    store.request_approval(
        run.id,
        PreparedToolCall(
            call,
            True,
            target=str((tmp_path / "demo.txt").resolve()),
            preview="--- /dev/null\n+++ demo.txt\n+hello",
            baseline_sha256=None,
            metadata={"operation": "write"},
        ),
    )

    response = client.get(f"/api/sessions/{session.id}/snapshot")

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["tools"] == [
        {
            "tool_call_id": "call-1",
            "run_id": run.id,
            "assistant_message_id": "assistant-1",
            "call_order": 0,
            "name": "write_file",
            "input": {
                "operation": "write",
                "path": "demo.txt",
                "content": "hello\n",
            },
            "requires_approval": True,
            "approval_status": "pending",
            "approval_decision": None,
            "approval_decided_at": None,
            "execution_state": "awaiting_approval",
            "result": None,
            "duration_ms": None,
        }
    ]
    assert snapshot["pending_approval"] == {
        "run_id": run.id,
        "tool_call_id": "call-1",
        "name": "write_file",
        "input": {
            "operation": "write",
            "path": "demo.txt",
            "content": "hello\n",
        },
        "target": str((tmp_path / "demo.txt").resolve()),
        "preview": "--- /dev/null\n+++ demo.txt\n+hello",
        "metadata": {"operation": "write"},
    }
    assert snapshot["interrupted_banner"] is None


def test_snapshot_json_schema_freezes_domain_enums_for_the_frontend() -> None:
    """Plain string fields would let Python and TypeScript protocol enums silently drift."""
    definitions = SessionSnapshotDto.model_json_schema()["$defs"]

    assert definitions["RunState"]["enum"] == [item.value for item in RunState]
    assert definitions["StopReason"]["enum"] == [item.value for item in StopReason]
    assert definitions["ApprovalStatus"]["enum"] == [item.value for item in ApprovalStatus]
    assert definitions["ApprovalDecision"]["enum"] == [item.value for item in ApprovalDecision]
    assert definitions["ToolExecutionState"]["enum"] == [item.value for item in ToolExecutionState]


def test_host_must_be_loopback_at_the_actual_server_port(tmp_path: Path) -> None:
    """Trusting only a hostname without its bound port leaves DNS-rebinding ambiguity."""
    client, _, _ = _make_client(tmp_path)

    wrong_port = client.get("/api/health", headers={"Host": "127.0.0.1:8124"})
    malicious = client.get("/api/health", headers={"Host": "attacker.invalid:8123"})

    assert wrong_port.status_code == 400
    assert malicious.status_code == 400


def test_explicit_loopback_development_origin_gets_narrow_cors_access(tmp_path: Path) -> None:
    """Development CORS must enable only the configured Vite origin and required method/header."""
    database = tmp_path / "state.db"
    store = SQLiteStore(database)
    store.initialize()
    app = create_app(
        store,
        None,
        {},
        server_port=SERVER_PORT,
        development_origin="http://localhost:5173/",
    )
    client = TestClient(app, base_url=BASE_URL)

    preflight = client.options(
        "/api/sessions",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "x-csrf-token" in preflight.headers["access-control-allow-headers"].lower()


def test_restart_invalidates_the_previous_process_token(tmp_path: Path) -> None:
    """Persisting the CSRF token would let a browser command a restarted process."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first, store, _ = _make_client(tmp_path)
    old_token = _token(first)
    restarted_app = create_app(
        store=store,
        coordinator=None,
        public_config={},
        event_publisher=EventPublisher(),
        server_port=SERVER_PORT,
    )
    restarted = TestClient(restarted_app, base_url=BASE_URL)

    rejected = restarted.post(
        "/api/sessions",
        json={"workspace": str(workspace), "title": None},
        headers=_mutation_headers(old_token),
    )
    new_token = _token(restarted)
    accepted = restarted.post(
        "/api/sessions",
        json={"workspace": str(workspace), "title": None},
        headers=_mutation_headers(new_token),
    )

    assert rejected.status_code == 403
    assert rejected.json()["detail"]["code"] == "PROCESS_TOKEN_INVALID"
    assert accepted.status_code == 201
