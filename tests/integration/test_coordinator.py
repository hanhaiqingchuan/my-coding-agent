from __future__ import annotations

import asyncio
import sqlite3

import pytest

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.errors import StoreError
from coding_agent.core.events import RunOutcome
from coding_agent.core.models import (
    ApprovalDecision,
    AssistantTurn,
    EffectStartResult,
    ModelStopReason,
    RunState,
    TextPart,
    ToolCall,
    ToolError,
    ToolResult,
    ToolUsePart,
    Usage,
)
from coding_agent.runtime.coordinator import RunMutationGate
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
    gate.register_cancellation(run_id, token)

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
