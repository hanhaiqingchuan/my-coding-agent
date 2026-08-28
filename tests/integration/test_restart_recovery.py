from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coding_agent.api.app import create_app
from coding_agent.core.errors import StoreError
from coding_agent.core.models import (
    ApprovalDecision,
    AssistantTurn,
    EffectStartResult,
    ModelStopReason,
    RunState,
    StopReason,
    TextPart,
    ToolCall,
    ToolUsePart,
    Usage,
)
from coding_agent.storage.sqlite import SQLiteStore

SERVER_PORT = 8123
BASE_URL = f"http://127.0.0.1:{SERVER_PORT}"


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    result = SQLiteStore(tmp_path / "state.db")
    result.initialize()
    return result


def _app(store: SQLiteStore, *, web_dist: Path | None = None):
    options = {"web_dist": web_dist} if web_dist is not None else {}
    return create_app(
        store,
        coordinator=None,
        public_config={},
        server_port=SERVER_PORT,
        **options,
    )


def _tool_turn() -> AssistantTurn:
    return AssistantTurn(
        id="assistant-tools",
        parts=(
            TextPart("I will make the requested changes."),
            ToolUsePart(
                ToolCall(
                    "call-write",
                    "write_file",
                    {"path": "result.txt", "content": "finished\n"},
                )
            ),
            ToolUsePart(
                ToolCall(
                    "call-command",
                    "run_command",
                    {"command": "true", "cwd": ".", "reason": "verify"},
                )
            ),
        ),
        stop_reason=ModelStopReason.TOOL_USE,
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def test_lifespan_recovers_model_streaming_before_the_first_request(store: SQLiteStore) -> None:
    """Moving recovery after request admission could expose a stale active run."""
    session = store.create_session("/tmp/workspace", "streaming")
    run = store.begin_run(session.id, "continue", {}, "start-1", "hash-1")
    store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
    store.transition_run(
        run.id,
        {RunState.BUILDING_CONTEXT},
        RunState.MODEL_STREAMING,
        None,
        None,
    )

    with TestClient(_app(store), base_url=BASE_URL) as client:
        snapshot = client.get(f"/api/sessions/{session.id}/snapshot").json()

    assert snapshot["active_run"] is None
    assert snapshot["session"]["requires_recovery_ack"] is False
    assert snapshot["interrupted_banner"] == {
        "run_id": run.id,
        "stop_reason": StopReason.SERVER_RESTART.value,
        "requires_recovery_ack": False,
    }
    assert [message["role"] for message in snapshot["messages"]] == ["user"]


def test_lifespan_cancels_unstarted_approval_group_without_an_ack_gate(
    store: SQLiteStore,
) -> None:
    """Replaying or leaving an approved-looking queued tool could create a new side effect."""
    session = store.create_session("/tmp/workspace", "approval")
    run = store.begin_run(session.id, "change files", {}, "start-1", "hash-1")
    store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
    store.transition_run(run.id, {RunState.BUILDING_CONTEXT}, RunState.MODEL_STREAMING, None, None)
    store.stage_tool_group(run.id, _tool_turn())

    with TestClient(_app(store), base_url=BASE_URL) as client:
        snapshot = client.get(f"/api/sessions/{session.id}/snapshot").json()

    assert snapshot["active_run"] is None
    assert snapshot["session"]["requires_recovery_ack"] is False
    assert [tool["execution_state"] for tool in snapshot["tools"]] == [
        "cancelled",
        "skipped",
    ]
    assert [tool["approval_status"] for tool in snapshot["tools"]] == [
        "cancelled",
        "cancelled",
    ]
    assert [message["role"] for message in snapshot["messages"]] == [
        "user",
        "assistant",
        "tool",
        "tool",
    ]


def test_lifespan_marks_started_tool_unknown_and_blocks_until_recovery_ack(
    store: SQLiteStore,
) -> None:
    """Treating a started effect as safe could duplicate an unknown write or process."""
    session = store.create_session("/tmp/workspace", "running")
    run = store.begin_run(session.id, "change files", {}, "start-1", "hash-1")
    store.transition_run(run.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None)
    store.transition_run(run.id, {RunState.BUILDING_CONTEXT}, RunState.MODEL_STREAMING, None, None)
    store.stage_tool_group(run.id, _tool_turn())
    store.resolve_approval(
        run.id,
        "call-write",
        ApprovalDecision.APPROVE,
        "approve-1",
        "approve-hash-1",
    )
    assert store.begin_effect(run.id, "call-write") is EffectStartResult.STARTED

    with TestClient(_app(store), base_url=BASE_URL) as client:
        snapshot = client.get(f"/api/sessions/{session.id}/snapshot").json()

    assert snapshot["active_run"] is None
    assert snapshot["session"]["requires_recovery_ack"] is True
    assert [tool["execution_state"] for tool in snapshot["tools"]] == [
        "unknown",
        "skipped",
    ]
    assert "unknown" in snapshot["tools"][0]["result"]["content"]
    assert "workspace" in snapshot["tools"][0]["result"]["content"]
    assert "detached" in snapshot["tools"][0]["result"]["content"]
    with pytest.raises(StoreError, match="acknowledged") as blocked:
        store.begin_run(session.id, "retry", {}, "start-2", "hash-2")
    assert blocked.value.code == "RECOVERY_ACK_REQUIRED"


def test_production_app_serves_the_built_spa_without_shadowing_api(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    """Omitting or broadly mounting static files would break the single-process UI."""
    web_dist = tmp_path / "dist"
    assets = web_dist / "assets"
    assets.mkdir(parents=True)
    (web_dist / "index.html").write_text("<main>offline agent</main>", encoding="utf-8")
    (assets / "app.js").write_text("window.agentReady = true;", encoding="utf-8")

    with TestClient(_app(store, web_dist=web_dist), base_url=BASE_URL) as client:
        root = client.get("/")
        asset = client.get("/assets/app.js")
        health = client.get("/api/health")

    assert root.status_code == 200
    assert "offline agent" in root.text
    assert asset.status_code == 200
    assert "agentReady" in asset.text
    assert health.json() == {"status": "ok"}


def test_snapshot_keeps_a_stopped_stream_draft_visible_without_committing_it_to_context(
    store: SQLiteStore,
) -> None:
    """Dropping interrupted text from snapshots would erase output the user already saw."""
    session = store.create_session("/tmp/workspace", "stopped stream")
    run = store.begin_run(session.id, "stream", {}, "start-1", "hash-1")
    partial = AssistantTurn(
        id="partial-answer",
        parts=(TextPart("visible partial answer"),),
        stop_reason=ModelStopReason.END_TURN,
        usage=Usage(),
    )
    store.record_interrupted_turn(run.id, partial)
    store.request_cancellation(run.id, "stop-1", "stop-hash-1")
    store.transition_run(
        run.id,
        {RunState.CANCELLING},
        RunState.CANCELLED,
        StopReason.USER_STOP,
        None,
    )

    snapshot = store.load_snapshot(session.id)

    assert [message.status.value for message in snapshot.messages] == [
        "committed",
        "interrupted",
    ]
    assert [message.role for message in store.load_committed_transcript(session.id)] == ["user"]
