from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from coding_agent.config import AppSettings
from coding_agent.context import Compactor, ContextBuilder
from coding_agent.core.errors import StoreError
from coding_agent.core.models import (
    ApprovalDecision,
    AssistantTurn,
    ModelStopReason,
    RunState,
    StopReason,
    TextPart,
    ToolCall,
    ToolUsePart,
    Usage,
)
from coding_agent.model.protocol import ModelTransportError
from coding_agent.model.retry import RetryingInvoker
from coding_agent.runtime.approval import ApprovalGate
from coding_agent.runtime.coordinator import RunCoordinator, RunMutationGate
from coding_agent.runtime.loop import AgentLoop
from coding_agent.runtime.publisher import EventPublisher
from coding_agent.storage.sqlite import SQLiteStore
from tests.fakes.model import BlockingModel, ScriptedModel
from tests.fakes.tools import BlockingTools, RecordingTools


def _final_turn(text: str = "done") -> AssistantTurn:
    return AssistantTurn(
        "turn-final",
        (TextPart(text),),
        ModelStopReason.END_TURN,
        Usage(8, 2),
    )


def _tool_turn() -> AssistantTurn:
    return AssistantTurn(
        "turn-tools",
        (
            ToolUsePart(
                ToolCall(
                    "call-write",
                    "write_file",
                    {"operation": "write", "path": "out.txt", "content": "new"},
                )
            ),
            ToolUsePart(ToolCall("call-command", "run_command", {"command": "true"})),
        ),
        ModelStopReason.TOOL_USE,
        Usage(10, 4),
    )


def _make_coordinator(
    tmp_path: Path,
    settings: AppSettings,
    model,
    *,
    approval: ApprovalGate | None = None,
    tools: RecordingTools | None = None,
    invoker: RetryingInvoker | None = None,
) -> tuple[RunCoordinator, SQLiteStore, str, ApprovalGate, RecordingTools]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    session = store.create_session(str(workspace), "stop")
    publisher = EventPublisher()
    mutation_gate = RunMutationGate(store, publisher)
    approval_gate = approval or ApprovalGate(auto_approve=True)
    tool_registry = tools or RecordingTools()
    loop = AgentLoop(
        store=store,
        context_builder=ContextBuilder(),
        compactor=Compactor(ScriptedModel([]), store, model="scripted-compactor"),
        model=model,
        invoker=invoker or RetryingInvoker(),
        tools=tool_registry,
        approval_gate=approval_gate,
        publisher=publisher,
        mutation_gate=mutation_gate,
        settings=settings,
    )
    coordinator = RunCoordinator(
        store=store,
        mutation_gate=mutation_gate,
        runner=loop,
        config_snapshot={},
        approval_gate=approval_gate,
    )
    return coordinator, store, session.id, approval_gate, tool_registry


def _assert_cancelled(store: SQLiteStore, run_id: str) -> None:
    run = store.get_run(run_id)
    assert run.state is RunState.CANCELLED
    assert run.stop_reason is StopReason.USER_STOP
    assert run.cancellation_requested_at is not None


@pytest.mark.asyncio
async def test_replayed_start_does_not_duplicate_model_approval_or_effects(
    tmp_path: Path,
    valid_settings: AppSettings,
) -> None:
    """One durable start receipt must own one complete model/tool execution chain."""
    model = ScriptedModel([_tool_turn(), _final_turn()])
    coordinator, store, session_id, _, tools = _make_coordinator(
        tmp_path,
        valid_settings,
        model,
    )

    first, replay = await asyncio.gather(
        coordinator.start_run(session_id, "task", "same-start"),
        coordinator.start_run(session_id, "task", "same-start"),
    )
    await coordinator.wait_for_run(first.id)

    assert replay.id == first.id
    assert model.call_count == 2
    assert tools.executed == ["call-write", "call-command"]
    event_types = [event.type for event in store.events_after(session_id, 0)]
    assert event_types.count("approval.requested") == 2
    assert event_types.count("approval.resolved") == 2


@pytest.mark.asyncio
async def test_coordinator_stop_during_model_stream_rejects_late_completion(
    tmp_path: Path,
    valid_settings: AppSettings,
) -> None:
    """A model response arriving after persisted Stop must not become canonical or complete."""
    model = BlockingModel(_final_turn("late"))
    coordinator, store, session_id, _, _ = _make_coordinator(tmp_path, valid_settings, model)
    run = await coordinator.start_run(session_id, "task", "start")
    await model.started.wait()

    accepted = await coordinator.stop_run(run.id, "stop")
    model.release.set()
    await coordinator.wait_for_run(run.id)

    assert accepted.state is RunState.CANCELLING
    assert accepted.cancellation_requested_at is not None
    _assert_cancelled(store, run.id)
    assert [message.role for message in store.load_committed_transcript(session_id)] == ["user"]


@pytest.mark.asyncio
async def test_coordinator_stop_during_retry_wait_prevents_another_model_attempt(
    tmp_path: Path,
    valid_settings: AppSettings,
) -> None:
    """A Stop accepted in backoff must cancel the wait before the next request begins."""
    sleep_started = asyncio.Event()

    async def blocking_sleep(_: float) -> None:
        sleep_started.set()
        await asyncio.Event().wait()

    model = ScriptedModel([ModelTransportError(True, OSError("offline")), _final_turn()])
    coordinator, store, session_id, _, _ = _make_coordinator(
        tmp_path,
        valid_settings,
        model,
        invoker=RetryingInvoker(sleep=blocking_sleep, jitter_ratio=0),
    )
    run = await coordinator.start_run(session_id, "task", "start")
    await sleep_started.wait()

    accepted = await coordinator.stop_run(run.id, "stop")
    await coordinator.wait_for_run(run.id)

    assert accepted.state is RunState.CANCELLING
    _assert_cancelled(store, run.id)
    assert model.call_count == 1


@pytest.mark.asyncio
async def test_coordinator_stop_during_approval_blocks_late_decision_and_effect(
    tmp_path: Path,
    valid_settings: AppSettings,
) -> None:
    """Approval arriving after Stop must not revive or execute the frozen tool call."""
    approvals = ApprovalGate()
    model = ScriptedModel([_tool_turn()])
    coordinator, store, session_id, _, tools = _make_coordinator(
        tmp_path,
        valid_settings,
        model,
        approval=approvals,
    )
    run = await coordinator.start_run(session_id, "task", "start")
    requested = await asyncio.wait_for(approvals.next_request(), timeout=0.1)

    accepted = await coordinator.stop_run(run.id, "stop")
    with pytest.raises(StoreError, match="approval cannot be changed during cancellation"):
        await coordinator.resolve_approval(
            run.id,
            requested.call.id,
            decision=ApprovalDecision.APPROVE,
            client_command_id="late-approval",
        )
    await coordinator.wait_for_run(run.id)

    assert accepted.state is RunState.CANCELLING
    _assert_cancelled(store, run.id)
    assert tools.execution_count == 0


@pytest.mark.asyncio
async def test_coordinator_approval_is_not_persisted_again_by_the_loop(
    tmp_path: Path,
    valid_settings: AppSettings,
) -> None:
    """The loop must not create a second command after Coordinator audited the decision."""
    approvals = ApprovalGate()
    model = ScriptedModel([_tool_turn(), _final_turn()])
    coordinator, store, session_id, _, tools = _make_coordinator(
        tmp_path,
        valid_settings,
        model,
        approval=approvals,
    )
    run = await coordinator.start_run(session_id, "task", "start")
    requested = await asyncio.wait_for(approvals.next_request(), timeout=0.1)

    await coordinator.resolve_approval(
        run.id,
        requested.call.id,
        ApprovalDecision.APPROVE,
        "approve-write",
    )
    requested = await asyncio.wait_for(approvals.next_request(), timeout=0.1)
    await coordinator.resolve_approval(
        run.id,
        requested.call.id,
        ApprovalDecision.APPROVE,
        "approve-command",
    )
    await coordinator.wait_for_run(run.id)

    finished = store.get_run(run.id)
    assert finished.state is RunState.COMPLETED
    assert tools.executed == ["call-write", "call-command"]


@pytest.mark.asyncio
async def test_coordinator_stop_after_effect_start_preserves_real_settlement(
    tmp_path: Path,
    valid_settings: AppSettings,
) -> None:
    """An effect that won the start race must keep its actual result while later calls skip."""
    tools = BlockingTools()
    coordinator, store, session_id, _, _ = _make_coordinator(
        tmp_path,
        valid_settings,
        ScriptedModel([_tool_turn()]),
        tools=tools,
    )
    run = await coordinator.start_run(session_id, "task", "start")
    await tools.started.wait()

    accepted = await coordinator.stop_run(run.id, "stop")
    tools.release.set()
    await coordinator.wait_for_run(run.id)

    assert accepted.state is RunState.CANCELLING
    _assert_cancelled(store, run.id)
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT execution_state FROM tool_executions ORDER BY call_order"
        ).fetchall()
    assert [row[0] for row in rows] == ["succeeded", "skipped"]
