from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

import pytest

from coding_agent.config import AppSettings, ConfigurationError
from coding_agent.context import Compactor, ContextBuilder
from coding_agent.context.estimator import ESTIMATOR_ID, estimate_input_tokens
from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.events import RunOutcome
from coding_agent.core.models import (
    ApprovalDecision,
    AssistantTurn,
    ContextSnapshot,
    ErrorKind,
    MessageStatus,
    ModelStopReason,
    RunState,
    StopReason,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolError,
    ToolResult,
    ToolUsePart,
    Usage,
)
from coding_agent.model import ModelMessage, anthropic_messages
from coding_agent.model.anthropic_messages import AnthropicMessagesModel, _compile_messages
from coding_agent.model.protocol import (
    ModelAPIError,
    ModelProtocolError,
    ModelRequest,
    ModelTransportError,
    TextDelta,
)
from coding_agent.model.retry import RetryingInvoker
from coding_agent.runtime.approval import ApprovalGate
from coding_agent.runtime.coordinator import RunMutationGate
from coding_agent.runtime.loop import AgentLoop
from coding_agent.runtime.publisher import (
    AssistantDelta,
    AssistantThinkingClosed,
    AssistantThinkingDelta,
    EventPublisher,
    ToolOutputDelta,
)
from coding_agent.storage.sqlite import SQLiteStore
from coding_agent.tools.registry import ToolRegistry
from tests.fakes.model import BlockingModel, ScriptedModel
from tests.fakes.tools import BlockingTools, CancellationRaisingTools, RecordingTools
from tests.fixtures.anthropic_events import two_tool_use_event_stream


class _FakeStream:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self._events = events

    def __aiter__(self) -> AsyncIterator[dict[str, object]]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[dict[str, object]]:
        for event in self._events:
            yield event


class _FakeMessages:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self._events = events

    async def create(self, **_kwargs: object) -> _FakeStream:
        return _FakeStream(self._events)


class _FakeClient:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.messages = _FakeMessages(events)


class PartialFailureModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request, on_text_delta, cancellation, **_kwargs):
        self.requests.append(request)
        emitted = on_text_delta(TextDelta(0, "partial draft"))
        if inspect.isawaitable(emitted):
            await emitted
        raise ModelProtocolError("BROKEN_STREAM", "connection ended without message_stop")


def _tool_turn(*calls: ToolCall, text: str = "") -> AssistantTurn:
    parts = ((TextPart(text),) if text else ()) + tuple(ToolUsePart(call) for call in calls)
    return AssistantTurn("turn-tools", parts, ModelStopReason.TOOL_USE, Usage(20, 5))


def _final_turn(text: str = "done") -> AssistantTurn:
    return AssistantTurn("turn-final", (TextPart(text),), ModelStopReason.END_TURN, Usage(30, 4))


def _turn(
    turn_id: str,
    stop_reason: ModelStopReason,
    *parts: TextPart | ToolUsePart,
) -> AssistantTurn:
    return AssistantTurn(turn_id, parts, stop_reason, Usage(7, 3))


def _make_loop(
    tmp_path: Path,
    settings: AppSettings,
    script: list[AssistantTurn | Exception],
    *,
    tools: RecordingTools | None = None,
    approval: ApprovalGate | None = None,
    invoker: RetryingInvoker | None = None,
    prompt: str = "do the work",
    history: tuple[str, ...] = (),
    model_override=None,
) -> tuple[
    AgentLoop,
    SQLiteStore,
    str,
    str,
    ScriptedModel,
    RecordingTools,
    RunMutationGate,
]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    session = store.create_session(str(workspace), "loop")
    for index, text in enumerate(history, start=1):
        previous = store.begin_run(
            session.id,
            f"history request {index}",
            {},
            f"history-start-{index}",
            f"history-hash-{index}",
        )
        store.transition_run(
            previous.id, {RunState.STARTING}, RunState.BUILDING_CONTEXT, None, None
        )
        store.transition_run(
            previous.id,
            {RunState.BUILDING_CONTEXT},
            RunState.MODEL_STREAMING,
            None,
            None,
        )
        store.commit_final_turn(
            previous.id,
            _turn(f"history-turn-{index}", ModelStopReason.END_TURN, TextPart(text)),
        )
        store.transition_run(
            previous.id,
            {RunState.MODEL_STREAMING},
            RunState.COMPLETED,
            StopReason.COMPLETED,
            None,
        )
    run = store.begin_run(session.id, prompt, {}, "start", "start-hash")
    model = model_override or ScriptedModel(script)
    compaction_model = ScriptedModel([])
    publisher = EventPublisher()
    mutation_gate = RunMutationGate(store, publisher)
    tool_fake = tools or RecordingTools()
    loop = AgentLoop(
        store=store,
        context_builder=ContextBuilder(),
        compactor=Compactor(compaction_model, store, model="scripted-compactor"),
        model=model,
        invoker=invoker or RetryingInvoker(),
        tools=tool_fake,
        approval_gate=approval or ApprovalGate(auto_approve=True),
        publisher=publisher,
        mutation_gate=mutation_gate,
        settings=settings,
    )
    return loop, store, session.id, run.id, model, tool_fake, mutation_gate


@pytest.mark.asyncio
async def test_live_deltas_include_run_and_distinct_draft_epochs(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Reusing an epoch across attempts or tools would let reconnect stitch stale drafts."""
    loop, _, session_id, run_id, _, _, mutation_gate = _make_loop(
        tmp_path,
        valid_settings,
        [
            _tool_turn(ToolCall("call-read", "read_file", {"path": "seed.txt"}), text="reading"),
            _final_turn("finished"),
        ],
    )
    publisher = mutation_gate.event_publisher
    async with publisher.session_guard(session_id):
        subscription = publisher.subscribe_locked(session_id)

    await loop.run(run_id, session_id, CancellationToken())
    published = []
    while True:
        try:
            published.append(await asyncio.wait_for(subscription.receive(), timeout=0.01))
        except TimeoutError:
            break

    assistant = [item for item in published if isinstance(item, AssistantDelta)]
    tool = [item for item in published if isinstance(item, ToolOutputDelta)]
    assert [item.text for item in assistant] == ["reading", "finished"]
    assert len({item.draft_epoch for item in assistant}) == 2
    assert all(item.run_id == run_id and item.session_id == session_id for item in assistant)
    assert [(item.tool_call_id, item.text) for item in tool] == [("call-read", "output:call-read")]
    assert tool[0].run_id == run_id
    assert tool[0].draft_epoch


@pytest.mark.asyncio
async def test_thinking_streams_transient_events_without_durable_writes(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Reasoning streams live like text deltas but never lands in the durable event log."""
    turn = AssistantTurn(
        "turn-thinking",
        (ThinkingPart("Reasoning first."), TextPart("done")),
        ModelStopReason.END_TURN,
        Usage(30, 4),
    )
    loop, store, session_id, run_id, _, _, mutation_gate = _make_loop(
        tmp_path, valid_settings, [turn]
    )
    publisher = mutation_gate.event_publisher
    async with publisher.session_guard(session_id):
        subscription = publisher.subscribe_locked(session_id)

    await loop.run(run_id, session_id, CancellationToken())
    published = []
    while True:
        try:
            published.append(await asyncio.wait_for(subscription.receive(), timeout=0.01))
        except TimeoutError:
            break

    thinking_deltas = [item for item in published if isinstance(item, AssistantThinkingDelta)]
    thinking_closed = [item for item in published if isinstance(item, AssistantThinkingClosed)]
    text_deltas = [item for item in published if isinstance(item, AssistantDelta)]
    assert len(thinking_deltas) == 2
    assert "".join(delta.text for delta in thinking_deltas) == "Reasoning first."
    assert len(thinking_closed) == 1
    assert all(item.run_id == run_id and item.session_id == session_id for item in thinking_deltas)
    assert all(item.run_id == run_id for item in thinking_closed)
    assert thinking_closed[0].index == 0
    epochs = {item.draft_epoch for item in [*thinking_deltas, *thinking_closed, *text_deltas]}
    assert len(epochs) == 1
    assert next(iter(epochs))
    thinking_index = published.index(thinking_deltas[0])
    closed_index = published.index(thinking_closed[0])
    text_index = published.index(text_deltas[0])
    assert thinking_index < closed_index < text_index

    events = store.events_after(session_id, 0)
    assert all("thinking" not in event.type for event in events)
    with store.connection() as connection:
        thinking_rows = connection.execute(
            "SELECT type FROM events WHERE type LIKE '%thinking%'"
        ).fetchall()
    assert thinking_rows == []
    transcript = store.load_committed_transcript(session_id)
    assert transcript[-1].parts == (ThinkingPart("Reasoning first."), TextPart("done"))


@pytest.mark.asyncio
async def test_thinking_only_turn_keeps_empty_response_semantics(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """A reasoning-only answer is still silence: retry once, then stop with EMPTY_RESPONSE."""
    thinking_only = AssistantTurn(
        "turn-thinking-only",
        (ThinkingPart("Only reasoning, no answer."),),
        ModelStopReason.END_TURN,
        Usage(30, 4),
    )
    loop, store, session_id, run_id, model, _, _ = _make_loop(
        tmp_path, valid_settings, [thinking_only, thinking_only]
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.stop(StopReason.EMPTY_RESPONSE)
    assert model.call_count == 2
    transcript = store.load_committed_transcript(session_id)
    assert not any(
        isinstance(part, ThinkingPart) for message in transcript for part in message.parts
    )


@pytest.mark.asyncio
async def test_scripted_model_runs_read_write_command_then_commits_the_group(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Executing out of order or exposing a partial tool group would corrupt model history."""
    calls = (
        ToolCall("call-read", "read_file", {"path": "seed.txt"}),
        ToolCall("call-write", "write_file", {"operation": "write", "path": "out.txt"}),
        ToolCall("call-command", "run_command", {"command": "true"}),
    )
    statuses_during_execution: list[str] = []
    store_box: list[SQLiteStore] = []

    def observe_pending(_prepared) -> None:
        with store_box[0].connection() as connection:
            statuses_during_execution.append(
                connection.execute(
                    "SELECT status FROM messages WHERE id = 'turn-tools'"
                ).fetchone()[0]
            )

    tools = RecordingTools(observe_pending)
    approval = ApprovalGate()
    loop, store, session_id, run_id, model, _, _ = _make_loop(
        tmp_path,
        valid_settings,
        [_tool_turn(*calls, text="Everything is complete."), _final_turn("finished")],
        tools=tools,
        approval=approval,
    )
    store_box.append(store)

    running = asyncio.create_task(loop.run(run_id, session_id, CancellationToken()))
    write_request = await approval.next_request()
    assert write_request.call.id == "call-write"
    approval.resolve(write_request.call.id, ApprovalDecision.APPROVE)
    command_request = await approval.next_request()
    assert command_request.call.id == "call-command"
    approval.resolve(command_request.call.id, ApprovalDecision.APPROVE)
    outcome = await running

    assert outcome == RunOutcome.complete()
    assert tools.executed == ["call-read", "call-write", "call-command"]
    assert statuses_during_execution == [MessageStatus.PENDING_TOOLS.value] * 3
    transcript = store.load_committed_transcript(session_id)
    assert [message.role for message in transcript] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "tool",
        "assistant",
    ]
    assert transcript[1].status is MessageStatus.COMMITTED
    assert [message.tool_call_id for message in transcript[2:5]] == [
        "call-read",
        "call-write",
        "call-command",
    ]
    assert model.call_count == 2
    tool_results = [
        part
        for message in model.requests[1].messages
        if isinstance(message, ModelMessage)
        for part in message.parts
        if isinstance(part, ToolResult)
    ]
    assert [item.tool_call_id for item in tool_results] == [
        "call-read",
        "call-write",
        "call-command",
    ]
    events = store.events_after(session_id, 0)
    assert [event.type for event in events].count("approval.requested") == 2
    assert [event.type for event in events].count("approval.resolved") == 2
    finished = store.get_run(run_id)
    assert finished.state is RunState.COMPLETED
    assert finished.stop_reason is StopReason.COMPLETED
    with store.connection() as connection:
        requests = connection.execute(
            "SELECT round_no, kind, result, attempt_count, input_tokens, output_tokens "
            "FROM model_requests WHERE run_id = ? ORDER BY round_no",
            (run_id,),
        ).fetchall()
    assert [tuple(row) for row in requests] == [
        (1, "main", "succeeded", 1, 20, 5),
        (2, "main", "succeeded", 1, 30, 4),
    ]


@pytest.mark.asyncio
async def test_trusted_mode_approval_is_still_fully_audited(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Bypassing audit in trusted mode would make write effects unaccountable."""
    loop, store, session_id, run_id, _, tools, _ = _make_loop(
        tmp_path,
        valid_settings,
        [
            _tool_turn(ToolCall("call-write", "write_file", {"operation": "write", "path": "a"})),
            _final_turn(),
        ],
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.complete()
    assert tools.executed == ["call-write"]
    event_types = [event.type for event in store.events_after(session_id, 0)]
    assert event_types.count("approval.requested") == 1
    assert event_types.count("approval.resolved") == 1


@pytest.mark.asyncio
async def test_loop_executes_the_real_read_write_and_command_registry(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """A loop compatible only with its test double would fail at the real tool dispatch boundary."""
    calls = (
        ToolCall("real-read", "read_file", {"path": "seed.txt"}),
        ToolCall(
            "real-write",
            "write_file",
            {"operation": "write", "path": "out.txt", "content": "written\n"},
        ),
        ToolCall("real-command", "run_command", {"command": "test -f out.txt"}),
    )
    loop, store, session_id, run_id, _, _, _ = _make_loop(
        tmp_path,
        valid_settings,
        [_tool_turn(*calls), _final_turn()],
        tools=ToolRegistry(),
    )
    (tmp_path / "workspace" / "seed.txt").write_text("seed\n", encoding="utf-8")
    (tmp_path / "workspace" / "out.txt").write_text("old\n", encoding="utf-8")

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.complete()
    assert (tmp_path / "workspace" / "out.txt").read_text(encoding="utf-8") == "written\n"
    with store.connection() as connection:
        baseline = connection.execute(
            "SELECT baseline_sha256 FROM tool_executions WHERE tool_call_id = 'real-write'"
        ).fetchone()[0]
    assert baseline is not None


@pytest.mark.asyncio
async def test_reject_marks_current_rejected_skips_rest_and_returns_results_to_model(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Treating Reject as a generic failure could execute later calls or lose paired history."""
    approval = ApprovalGate()
    calls = (
        ToolCall("call-write", "write_file", {"operation": "write", "path": "out.txt"}),
        ToolCall("call-command", "run_command", {"command": "true"}),
    )
    loop, store, session_id, run_id, model, tools, _ = _make_loop(
        tmp_path,
        valid_settings,
        [_tool_turn(*calls), _final_turn("replanned")],
        approval=approval,
    )

    running = asyncio.create_task(loop.run(run_id, session_id, CancellationToken()))
    requested = await approval.next_request()
    approval.resolve(requested.call.id, ApprovalDecision.REJECT)
    outcome = await running

    assert outcome == RunOutcome.complete()
    assert tools.executed == []
    with store.connection() as connection:
        states = connection.execute(
            "SELECT execution_state FROM tool_executions ORDER BY call_order"
        ).fetchall()
    assert [row[0] for row in states] == ["rejected", "skipped"]
    returned = [
        part
        for message in model.requests[1].messages
        for part in message.parts
        if isinstance(part, ToolResult)
    ]
    assert [item.error.code for item in returned if item.error] == [
        "TOOL_REJECTED",
        "TOOL_SKIPPED",
    ]


@pytest.mark.asyncio
async def test_unknown_tool_is_a_normal_result_and_never_requests_approval(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Sending an unknown tool to approval would turn a correctable model error into a stall."""
    approval = ApprovalGate()
    loop, store, session_id, run_id, model, tools, _ = _make_loop(
        tmp_path,
        valid_settings,
        [
            _tool_turn(ToolCall("call-unknown", "delete_everything", {})),
            _final_turn("corrected"),
        ],
        approval=approval,
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.complete()
    assert tools.execution_count == 0
    assert approval.pending == ()
    assert "approval.requested" not in [event.type for event in store.events_after(session_id, 0)]
    returned = [
        part
        for message in model.requests[1].messages
        for part in message.parts
        if isinstance(part, ToolResult)
    ]
    assert returned[0].error is not None
    assert returned[0].error.code == "UNKNOWN_TOOL"


@pytest.mark.asyncio
async def test_invalid_write_arguments_return_an_error_without_requesting_approval(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Requesting approval before validation would ask the user to approve an unusable write."""
    loop, store, session_id, run_id, model, tools, _ = _make_loop(
        tmp_path,
        valid_settings,
        [
            _tool_turn(ToolCall("call-invalid-write", "write_file", {"invalid": True})),
            _final_turn("corrected"),
        ],
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.complete()
    assert tools.execution_count == 0
    assert "approval.requested" not in [event.type for event in store.events_after(session_id, 0)]
    returned = [
        part
        for message in model.requests[1].messages
        for part in message.parts
        if isinstance(part, ToolResult)
    ]
    assert returned[0].error is not None
    assert returned[0].error.code == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_stop_while_awaiting_approval_cancels_current_and_skips_rest(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """A stopped approval waiter must not leave a pending group or execute queued effects."""
    approval = ApprovalGate()
    calls = (
        ToolCall("call-write", "write_file", {"operation": "write", "path": "out.txt"}),
        ToolCall("call-command", "run_command", {"command": "true"}),
    )
    loop, store, session_id, run_id, _, tools, mutation_gate = _make_loop(
        tmp_path, valid_settings, [_tool_turn(*calls)], approval=approval
    )
    cancellation = CancellationToken()

    running = asyncio.create_task(loop.run(run_id, session_id, cancellation))
    await approval.next_request()
    await mutation_gate.request_stop(run_id, "stop-awaiting")
    outcome = await running

    assert outcome == RunOutcome.cancel()
    assert tools.executed == []
    with store.connection() as connection:
        states = connection.execute(
            "SELECT execution_state FROM tool_executions ORDER BY call_order"
        ).fetchall()
    assert [row[0] for row in states] == ["cancelled", "skipped"]
    assert all(
        message.status is MessageStatus.COMMITTED
        for message in store.load_committed_transcript(session_id)
    )


@pytest.mark.asyncio
async def test_stop_after_effect_start_keeps_real_result_and_skips_queued_calls(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Overwriting an already-started effect as cancelled would hide a completed side effect."""
    tools = BlockingTools()
    calls = (
        ToolCall("call-write", "write_file", {"operation": "write", "path": "out.txt"}),
        ToolCall("call-command", "run_command", {"command": "true"}),
    )
    loop, store, session_id, run_id, _, _, mutation_gate = _make_loop(
        tmp_path, valid_settings, [_tool_turn(*calls)], tools=tools
    )
    cancellation = CancellationToken()

    running = asyncio.create_task(loop.run(run_id, session_id, cancellation))
    await tools.started.wait()
    await mutation_gate.request_stop(run_id, "stop-running")
    tools.release.set()
    outcome = await running

    assert outcome == RunOutcome.cancel()
    with store.connection() as connection:
        states = connection.execute(
            "SELECT execution_state FROM tool_executions ORDER BY call_order"
        ).fetchall()
    assert [row[0] for row in states] == ["succeeded", "skipped"]


@pytest.mark.asyncio
async def test_stop_after_effect_start_records_cooperative_tool_cancellation(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Letting a cancellation exception bypass settlement would strand the pending tool group."""
    tools = CancellationRaisingTools()
    calls = (
        ToolCall("call-write", "write_file", {"operation": "write", "path": "out.txt"}),
        ToolCall("call-command", "run_command", {"command": "true"}),
    )
    loop, store, session_id, run_id, _, _, mutation_gate = _make_loop(
        tmp_path, valid_settings, [_tool_turn(*calls)], tools=tools
    )
    cancellation = CancellationToken()

    running = asyncio.create_task(loop.run(run_id, session_id, cancellation))
    await tools.started.wait()
    await mutation_gate.request_stop(run_id, "stop-running-cancel")
    outcome = await running

    assert outcome == RunOutcome.cancel()
    with store.connection() as connection:
        states = connection.execute(
            "SELECT execution_state FROM tool_executions ORDER BY call_order"
        ).fetchall()
    assert [row[0] for row in states] == ["cancelled", "skipped"]


@pytest.mark.asyncio
async def test_stop_wins_over_late_final_model_turn(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """A late complete response must not overwrite a persisted Stop or enter canonical history."""
    model = BlockingModel(_final_turn("late answer"))
    loop, store, session_id, run_id, _, _, mutation_gate = _make_loop(
        tmp_path, valid_settings, [], model_override=model
    )
    cancellation = CancellationToken()

    running = asyncio.create_task(loop.run(run_id, session_id, cancellation))
    await model.started.wait()
    await mutation_gate.request_stop(run_id, "stop-model")
    model.release.set()
    outcome = await running

    assert outcome == RunOutcome.cancel()
    assert [message.role for message in store.load_committed_transcript(session_id)] == ["user"]


@pytest.mark.asyncio
async def test_stop_before_loop_registers_token_finishes_cancelled_without_model_call(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Missing persisted cancellation during registration would strand the run in CANCELLING."""
    loop, store, session_id, run_id, model, _, mutation_gate = _make_loop(
        tmp_path, valid_settings, [_final_turn("must not run")]
    )
    await mutation_gate.request_stop(run_id, "stop-before-registration")
    cancellation = CancellationToken()

    outcome = await loop.run(run_id, session_id, cancellation)

    assert outcome == RunOutcome.cancel()
    assert cancellation.cancelled is True
    assert model.call_count == 0
    finished = store.get_run(run_id)
    assert finished.state is RunState.CANCELLED
    assert finished.stop_reason is StopReason.USER_STOP
    assert [message.role for message in store.load_committed_transcript(session_id)] == ["user"]


@pytest.mark.asyncio
async def test_empty_response_retries_once_then_stops_with_typed_reason(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Completing on an empty turn would present silence as a successful agent result."""
    script = [
        _turn("empty-1", ModelStopReason.END_TURN),
        _turn("empty-2", ModelStopReason.END_TURN),
    ]
    loop, _, session_id, run_id, model, _, _ = _make_loop(tmp_path, valid_settings, script)

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.stop(StopReason.EMPTY_RESPONSE)
    assert model.call_count == 2


@pytest.mark.asyncio
async def test_one_empty_response_can_recover_on_the_semantic_retry(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Stopping on the first empty turn would discard the single allowed semantic retry."""
    loop, _, session_id, run_id, model, _, _ = _make_loop(
        tmp_path,
        valid_settings,
        [_turn("empty", ModelStopReason.END_TURN), _final_turn("recovered")],
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.complete()
    assert model.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("turn", "expected_reason"),
    [
        (
            _turn("truncated", ModelStopReason.MAX_TOKENS, TextPart("partial")),
            StopReason.OUTPUT_TRUNCATED,
        ),
        (
            _turn("refused", ModelStopReason.REFUSAL, TextPart("cannot comply")),
            StopReason.MODEL_REFUSAL,
        ),
        (
            _turn("paused", ModelStopReason.PAUSE_TURN, TextPart("paused")),
            StopReason.PAUSE_TURN,
        ),
    ],
)
async def test_special_text_outputs_are_interrupted_not_canonical(
    tmp_path: Path,
    valid_settings: AppSettings,
    turn: AssistantTurn,
    expected_reason: StopReason,
) -> None:
    """Committing special provider output would feed a non-final draft back as canonical history."""
    loop, store, session_id, run_id, _, tools, _ = _make_loop(tmp_path, valid_settings, [turn])

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.stop(expected_reason)
    assert tools.execution_count == 0
    assert [message.role for message in store.load_committed_transcript(session_id)] == ["user"]
    with store.connection() as connection:
        draft = connection.execute(
            "SELECT status FROM messages WHERE id = ?", (turn.id,)
        ).fetchone()
    assert draft[0] == MessageStatus.INTERRUPTED.value


@pytest.mark.asyncio
async def test_real_assembler_incomplete_tool_call_maps_to_stopped_and_keeps_usage(
    tmp_path: Path,
    valid_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collapsing assembler INCOMPLETE_TOOL_CALL into protocol failure loses policy and usage."""
    events = list(two_tool_use_event_stream())
    message_delta = events[-3]
    assert isinstance(message_delta["delta"], dict)
    message_delta["delta"]["stop_reason"] = "max_tokens"
    client = _FakeClient(events)
    monkeypatch.setattr(anthropic_messages, "AsyncAnthropic", lambda **_kwargs: client)
    model = AnthropicMessagesModel(valid_settings.model, api_key="test-only")
    loop, store, session_id, run_id, _, tools, _ = _make_loop(
        tmp_path, valid_settings, [], model_override=model
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.stop(StopReason.INCOMPLETE_TOOL_CALL)
    assert tools.execution_count == 0
    assert [message.role for message in store.load_committed_transcript(session_id)] == ["user"]
    with store.connection() as connection:
        request = connection.execute(
            "SELECT result, input_tokens, output_tokens, cache_creation_input_tokens, "
            "cache_read_input_tokens FROM model_requests WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert (
            connection.execute(
                "SELECT count(*) FROM tool_executions WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            == 0
        )
    assert tuple(request) == ("incomplete_tool_call", 21, 9, 5, 8)


@pytest.mark.asyncio
async def test_last_round_disables_tools_and_commits_text_with_max_rounds_outcome(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Leaving tools enabled on the budget boundary could start work the loop cannot finish."""
    settings = replace(valid_settings, agent=replace(valid_settings.agent, max_rounds=2))
    script = [
        _turn(
            "tool-round",
            ModelStopReason.TOOL_USE,
            ToolUsePart(ToolCall("call-read", "read_file", {"path": "a.txt"})),
        ),
        _turn("summary-round", ModelStopReason.END_TURN, TextPart("summary")),
    ]
    loop, store, session_id, run_id, model, tools, _ = _make_loop(tmp_path, settings, script)

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.stop(StopReason.MAX_ROUNDS)
    assert tools.execution_count == 1
    assert model.requests[0].tools
    assert model.requests[1].tools == ()
    assert "Directly summarize" in model.requests[1].system
    assert [message.role for message in store.load_committed_transcript(session_id)][-1] == (
        "assistant"
    )


@pytest.mark.asyncio
async def test_repetition_guard_returns_one_error_then_stops_fourth_proposal(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Executing every identical proposal would permit an unbounded repeated side effect."""
    script = [
        _turn(
            f"repeat-turn-{index}",
            ModelStopReason.TOOL_USE,
            ToolUsePart(ToolCall(f"repeat-call-{index}", "read_file", {"path": "same.txt"})),
        )
        for index in range(1, 5)
    ]
    loop, store, session_id, run_id, model, tools, _ = _make_loop(tmp_path, valid_settings, script)

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.stop(StopReason.DOOM_LOOP)
    assert model.call_count == 4
    assert tools.execution_count == 2
    transcript = store.load_committed_transcript(session_id)
    errors = [
        part.error.code
        for message in transcript
        for part in message.parts
        if isinstance(part, ToolResult) and part.error is not None
    ]
    assert errors == ["REPETITION_DETECTED"]


@pytest.mark.asyncio
async def test_tool_argument_errors_allow_only_two_correction_rounds(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Resetting the correction budget after every invalid call would loop forever."""
    script = [
        _turn(
            f"invalid-turn-{index}",
            ModelStopReason.TOOL_USE,
            ToolUsePart(
                ToolCall(
                    f"invalid-call-{index}",
                    "read_file",
                    {"invalid": True, "attempt": index},
                )
            ),
        )
        for index in range(1, 4)
    ]
    loop, store, session_id, run_id, model, tools, _ = _make_loop(tmp_path, valid_settings, script)

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.stop(StopReason.DOOM_LOOP)
    assert model.call_count == 3
    assert tools.execution_count == 0
    assert "approval.requested" not in [event.type for event in store.events_after(session_id, 0)]


@pytest.mark.asyncio
async def test_tool_arguments_can_recover_on_the_second_correction(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Stopping before the correction budget expires would reject a valid third proposal."""
    script = [
        _turn(
            "invalid-turn-1",
            ModelStopReason.TOOL_USE,
            ToolUsePart(ToolCall("invalid-call-1", "read_file", {"invalid": True, "n": 1})),
        ),
        _turn(
            "invalid-turn-2",
            ModelStopReason.TOOL_USE,
            ToolUsePart(ToolCall("invalid-call-2", "read_file", {"invalid": True, "n": 2})),
        ),
        _turn(
            "valid-turn",
            ModelStopReason.TOOL_USE,
            ToolUsePart(ToolCall("valid-call", "read_file", {"path": "ok.txt"})),
        ),
        _final_turn("recovered"),
    ]
    loop, _, session_id, run_id, model, tools, _ = _make_loop(tmp_path, valid_settings, script)

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.complete()
    assert model.call_count == 4
    assert tools.executed == ["valid-call"]


@pytest.mark.asyncio
async def test_unusable_tool_arguments_become_a_correctable_tool_error(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Failing the run on complete but unusable arguments would deny the model its own fix."""
    unusable = AssistantTurn(
        "unusable-turn",
        (
            TextPart("running the suite"),
            ToolUsePart(ToolCall("call-read", "read_file", {"path": "seed.txt"})),
            ToolUsePart(ToolCall("call-unusable", "run_command", {})),
        ),
        ModelStopReason.TOOL_USE,
        Usage(20, 5),
        invalid_tool_arguments={
            "call-unusable": ToolError(
                "INVALID_TOOL_INPUT_JSON", "tool input is not valid JSON: Expecting value"
            )
        },
    )
    script = [
        unusable,
        _turn(
            "corrected-turn",
            ModelStopReason.TOOL_USE,
            ToolUsePart(ToolCall("call-corrected", "run_command", {"command": "pytest"})),
        ),
        _final_turn("verified"),
    ]
    loop, store, session_id, run_id, model, tools, _ = _make_loop(tmp_path, valid_settings, script)

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.complete()
    assert model.call_count == 3
    assert tools.executed == ["call-read", "call-corrected"]
    approvals = [
        event.payload["tool_call_id"]
        for event in store.events_after(session_id, 0)
        if event.type == "approval.requested"
    ]
    assert approvals == ["call-corrected"]
    returned = [
        part
        for message in model.requests[1].messages
        for part in message.parts
        if isinstance(part, ToolResult)
    ]
    assert [(part.tool_call_id, part.ok) for part in returned] == [
        ("call-read", True),
        ("call-unusable", False),
    ]
    assert returned[1].error is not None
    assert returned[1].error.code == "INVALID_TOOL_INPUT_JSON"
    wire = _compile_messages(model.requests[1])
    assert wire[-1] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "call-read",
                "content": returned[0].content,
            },
            {
                "type": "tool_result",
                "tool_use_id": "call-unusable",
                "content": returned[1].content,
                "is_error": True,
            },
        ],
    }
    with store.connection() as connection:
        status = connection.execute(
            "SELECT status FROM messages WHERE id = 'unusable-turn'"
        ).fetchone()[0]
    assert status == MessageStatus.COMMITTED.value


@pytest.mark.asyncio
async def test_repeated_unusable_tool_arguments_end_the_run_terminally(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Answering every unusable input with a tool error forever would never end the run."""
    script = [
        AssistantTurn(
            f"unusable-turn-{index}",
            (ToolUsePart(ToolCall(f"call-unusable-{index}", name, {})),),
            ModelStopReason.TOOL_USE,
            Usage(20, 5),
            invalid_tool_arguments={
                f"call-unusable-{index}": ToolError(
                    "TOOL_INPUT_NOT_OBJECT", "tool input must be a JSON object"
                )
            },
        )
        for index, name in enumerate(("read_file", "run_command", "write_file"), start=1)
    ]
    loop, store, session_id, run_id, model, tools, _ = _make_loop(tmp_path, valid_settings, script)

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.stop(StopReason.DOOM_LOOP)
    assert model.call_count == 3
    assert tools.execution_count == 0
    run = store.get_run(run_id)
    assert run.state is RunState.STOPPED
    assert run.stop_reason is StopReason.DOOM_LOOP
    assert "approval.requested" not in [event.type for event in store.events_after(session_id, 0)]
    errors = [
        part.error.code
        for message in store.load_committed_transcript(session_id)
        for part in message.parts
        if isinstance(part, ToolResult) and part.error is not None
    ]
    assert errors == ["TOOL_INPUT_NOT_OBJECT"] * 3


@pytest.mark.asyncio
async def test_mandatory_context_overflow_never_calls_the_model(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Calling the model after deterministic overflow would violate the preflight budget gate."""
    settings = replace(
        valid_settings,
        model=replace(valid_settings.model, context_window=11_000),
    )
    loop, store, session_id, run_id, model, _, _ = _make_loop(
        tmp_path,
        settings,
        [],
        prompt="x" * 50_000,
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.fail(StopReason.CONTEXT_OVERFLOW, ErrorKind.CONTEXT_OVERFLOW)
    assert model.call_count == 0
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_requests WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.asyncio
async def test_retryable_model_failure_uses_one_request_record_with_attempt_metrics(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Recording retries as separate rounds would overcount model requests and lose wait cost."""

    async def no_wait(_: float) -> None:
        return None

    invoker = RetryingInvoker(
        max_attempts=2,
        initial_delay_seconds=2,
        max_delay_seconds=2,
        jitter_ratio=0,
        sleep=no_wait,
    )
    loop, store, session_id, run_id, model, _, _ = _make_loop(
        tmp_path,
        valid_settings,
        [ModelTransportError(True, ConnectionError()), _final_turn()],
        invoker=invoker,
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.complete()
    assert model.call_count == 2
    with store.connection() as connection:
        row = connection.execute(
            "SELECT attempt_count, network_retry_count, total_wait_ms "
            "FROM model_requests WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    assert tuple(row) == (2, 1, 2000)
    retry_events = [
        event
        for event in store.events_after(session_id, 0)
        if event.type == "model.retry_scheduled"
    ]
    assert len(retry_events) == 1
    assert retry_events[0].payload["attempt"] == 2
    assert retry_events[0].payload["delay_seconds"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ModelAPIError(401, "authentication_error", None, False),
            RunOutcome.fail(StopReason.AUTH_ERROR, ErrorKind.AUTH_ERROR),
        ),
        (
            ModelTransportError(False, ConnectionError()),
            RunOutcome.fail(StopReason.RETRY_EXHAUSTED, ErrorKind.RETRY_EXHAUSTED),
        ),
        (
            ModelProtocolError("BAD_STREAM", "broken"),
            RunOutcome.fail(StopReason.MODEL_PROTOCOL_ERROR, ErrorKind.MODEL_PROTOCOL_ERROR),
        ),
    ],
)
async def test_model_failures_map_to_typed_terminal_outcomes(
    tmp_path: Path,
    valid_settings: AppSettings,
    error: Exception,
    expected: RunOutcome,
) -> None:
    """Inferring model failure policy from exception text would make terminal state unstable."""
    loop, store, session_id, run_id, _, _, _ = _make_loop(tmp_path, valid_settings, [error])

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == expected
    with store.connection() as connection:
        record = connection.execute(
            "SELECT result, finished_at FROM model_requests WHERE run_id = ?", (run_id,)
        ).fetchone()
        round_count = connection.execute(
            "SELECT round_count FROM runs WHERE id = ?", (run_id,)
        ).fetchone()[0]
    assert record[0] == "failed"
    assert record[1] is not None
    assert round_count == 1


@pytest.mark.asyncio
async def test_tool_use_stop_reason_without_tool_block_is_protocol_failure(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Completing a malformed tool-use turn would accept a provider protocol violation."""
    malformed = _turn("malformed", ModelStopReason.TOOL_USE, TextPart("no call"))
    loop, _, session_id, run_id, _, tools, _ = _make_loop(tmp_path, valid_settings, [malformed])

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.fail(
        StopReason.MODEL_PROTOCOL_ERROR, ErrorKind.MODEL_PROTOCOL_ERROR
    )
    assert tools.execution_count == 0


@pytest.mark.asyncio
async def test_final_round_tool_call_is_not_staged_or_executed(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Accepting a tool call after definitions were removed could exceed the round budget."""
    settings = replace(valid_settings, agent=replace(valid_settings.agent, max_rounds=2))
    script = [
        _turn(
            "first-tool",
            ModelStopReason.TOOL_USE,
            ToolUsePart(ToolCall("call-first", "read_file", {"path": "a.txt"})),
        ),
        _turn(
            "illegal-final-tool",
            ModelStopReason.TOOL_USE,
            ToolUsePart(ToolCall("call-final", "read_file", {"path": "b.txt"})),
        ),
    ]
    loop, store, session_id, run_id, _, tools, _ = _make_loop(tmp_path, settings, script)

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome.state is RunState.STOPPED
    assert outcome.stop_reason is StopReason.MAX_ROUNDS
    assert outcome.error_kind is ErrorKind.MODEL_PROTOCOL_ERROR
    assert tools.executed == ["call-first"]
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM tool_executions WHERE tool_call_id = 'call-final'"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.asyncio
async def test_configuration_failure_has_config_typed_outcome(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Treating local configuration as retryable transport work would waste attempts."""
    loop, _, session_id, run_id, model, _, _ = _make_loop(
        tmp_path, valid_settings, [ConfigurationError("bad model config")]
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.fail(StopReason.CONFIG_ERROR, ErrorKind.CONFIG_ERROR)
    assert model.call_count == 1


@pytest.mark.asyncio
async def test_failed_stream_draft_is_interrupted_and_never_canonical(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Persisting a partial stream as committed would poison every later context build."""
    model = PartialFailureModel()
    loop, store, session_id, run_id, _, _, _ = _make_loop(
        tmp_path, valid_settings, [], model_override=model
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.fail(
        StopReason.MODEL_PROTOCOL_ERROR, ErrorKind.MODEL_PROTOCOL_ERROR
    )
    assert [message.role for message in store.load_committed_transcript(session_id)] == ["user"]
    with store.connection() as connection:
        draft = connection.execute(
            "SELECT parts_json, status FROM messages WHERE status = 'interrupted'"
        ).fetchone()
    assert "partial draft" in draft[0]
    assert draft[1] == MessageStatus.INTERRUPTED.value


def _summary_turn() -> AssistantTurn:
    fields = (
        "completed_work_and_evidence",
        "important_files_and_symbols",
        "tool_findings",
        "commands_and_tests",
        "failed_attempts",
        "remaining_work",
        "blockers",
        "next_steps",
    )
    summary = {field: [] for field in fields}
    summary["completed_work_and_evidence"] = ["older work summarized"]
    return _turn("compaction-summary", ModelStopReason.END_TURN, TextPart(json.dumps(summary)))


@pytest.mark.asyncio
async def test_provider_context_overflow_compacts_once_then_rebuilds(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Retrying the unchanged request would repeat provider overflow without reducing context."""
    overflow = _turn("overflow", ModelStopReason.MODEL_CONTEXT_WINDOW_EXCEEDED)
    loop, store, session_id, run_id, model, _, _ = _make_loop(
        tmp_path,
        valid_settings,
        [overflow, _summary_turn(), _final_turn("after compaction")],
        history=("a" * 4_000, "b" * 4_000, "c" * 4_000, "d" * 4_000),
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.complete()
    assert model.call_count == 3
    assert store.load_context_snapshot(session_id) is not None
    with store.connection() as connection:
        requests = connection.execute(
            "SELECT kind, input_tokens, output_tokens, finished_at, config_hash "
            "FROM model_requests WHERE run_id = ? ORDER BY started_at",
            (run_id,),
        ).fetchall()
        run_metrics = connection.execute(
            "SELECT round_count, input_tokens, output_tokens FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    assert [row[0] for row in requests] == ["main", "compaction", "main"]
    assert [(row[1], row[2]) for row in requests] == [(7, 3), (7, 3), (30, 4)]
    assert all(row[3] is not None for row in requests)
    assert requests[0][4] == requests[2][4]
    assert requests[1][4] != requests[0][4]
    assert tuple(run_metrics) == (2, 44, 10)


@pytest.mark.asyncio
async def test_second_provider_context_overflow_fails_without_another_compaction(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """Repeated overflow after rebuilding must not enter an unbounded compaction loop."""
    overflow_1 = _turn("overflow-1", ModelStopReason.MODEL_CONTEXT_WINDOW_EXCEEDED)
    overflow_2 = _turn("overflow-2", ModelStopReason.MODEL_CONTEXT_WINDOW_EXCEEDED)
    loop, store, session_id, run_id, model, _, _ = _make_loop(
        tmp_path,
        valid_settings,
        [overflow_1, _summary_turn(), overflow_2],
        history=("a" * 4_000, "b" * 4_000, "c" * 4_000, "d" * 4_000),
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.fail(StopReason.CONTEXT_OVERFLOW, ErrorKind.CONTEXT_OVERFLOW)
    assert model.call_count == 3
    with store.connection() as connection:
        kinds = connection.execute(
            "SELECT kind FROM model_requests WHERE run_id = ?", (run_id,)
        ).fetchall()
    assert [row[0] for row in kinds].count("compaction") == 1


@pytest.mark.asyncio
async def test_mandatory_view_above_soft_target_keeps_running_with_the_summary(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """A session that already has a summary must not die once user originals pass 60%.

    Every visible group is mandatory here, so nothing is left to compact and only the
    rolling summary pushes the estimate past the 60% soft target. Spec 7.4 item 10 keeps
    that view legal because it still fits the available input budget.
    """
    settings = replace(
        valid_settings,
        model=replace(valid_settings.model, context_window=6_000, max_output_tokens=1_000),
    )
    loop, store, session_id, run_id, model, _, _ = _make_loop(
        tmp_path,
        settings,
        [_final_turn("answered from the minimal legal view")],
        prompt="x" * 6_000,
        history=("first answer", "second answer", "third answer", "fourth answer"),
    )
    store.replace_context_snapshot(
        ContextSnapshot(
            session_id=session_id,
            covered_through_message_seq=4,
            summary="s" * 900,
            created_at=datetime.now(UTC),
            source_event_ids=("summarized-event",),
            model="scripted-compactor",
            estimator_id=ESTIMATOR_ID,
            token_estimate=300,
            compaction_above_target=True,
        )
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.complete()
    assert model.call_count == 1
    request = model.requests[0]
    assert [part.text for message in request.messages for part in message.parts] == [
        "history request 1",
        "history request 2",
        "s" * 900,
        "history request 3",
        "third answer",
        "history request 4",
        "fourth answer",
        "x" * 6_000,
    ]
    snapshot = store.load_context_snapshot(session_id)
    assert snapshot is not None
    assert snapshot.summary == "s" * 900
    assert snapshot.version == 1
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_requests WHERE run_id = ? AND kind = 'compaction'",
                (run_id,),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.asyncio
async def test_unexpected_store_failure_ends_the_run_and_frees_the_global_slot(
    tmp_path: Path, valid_settings: AppSettings, caplog: pytest.LogCaptureFixture
) -> None:
    """A run left non-terminal would hold the single process-wide active-run claim forever."""
    loop, store, session_id, run_id, _, _, _ = _make_loop(
        tmp_path,
        valid_settings,
        [_tool_turn(ToolCall("call-read", "read_file", {"path": "seed.txt"}))],
    )
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_tool_staging BEFORE INSERT ON events
            WHEN NEW.type = 'tool.group_staged'
            BEGIN SELECT RAISE(ABORT, 'injected staging failure'); END
            """
        )

    with caplog.at_level(logging.ERROR, logger="coding_agent.runtime.loop"):
        outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.fail(StopReason.INTERNAL_ERROR, ErrorKind.INTERNAL_ERROR)
    finished = store.get_run(run_id)
    assert finished.state is RunState.FAILED
    assert finished.stop_reason is StopReason.INTERNAL_ERROR
    assert finished.error_kind is ErrorKind.INTERNAL_ERROR
    assert finished.finished_at is not None
    assert [message.role for message in store.load_committed_transcript(session_id)] == ["user"]
    assert any(
        record.exc_info is not None and "injected staging failure" in str(record.exc_info[1])
        for record in caplog.records
    )
    store.begin_run(session_id, "next request", {}, "start-again", "hash-again")


@pytest.mark.asyncio
async def test_renamed_workspace_root_fails_the_run_without_a_pending_group(
    tmp_path: Path, valid_settings: AppSettings
) -> None:
    """An approved command can rename the workspace root between two tool rounds."""

    def rename_workspace(_prepared) -> None:
        (tmp_path / "workspace").rename(tmp_path / "workspace-renamed")

    tools = RecordingTools(rename_workspace)
    loop, store, session_id, run_id, _, _, _ = _make_loop(
        tmp_path,
        valid_settings,
        [
            _turn(
                "tools-first",
                ModelStopReason.TOOL_USE,
                ToolUsePart(ToolCall("call-first", "read_file", {"path": "a.txt"})),
            ),
            _turn(
                "tools-second",
                ModelStopReason.TOOL_USE,
                ToolUsePart(ToolCall("call-second", "read_file", {"path": "b.txt"})),
            ),
            _final_turn(),
        ],
        tools=tools,
    )

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.fail(StopReason.INTERNAL_ERROR, ErrorKind.INTERNAL_ERROR)
    assert store.get_run(run_id).state is RunState.FAILED
    assert tools.executed == ["call-first"]
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM messages WHERE status = ?",
                (MessageStatus.PENDING_TOOLS.value,),
            ).fetchone()[0]
            == 0
        )
    store.begin_run(session_id, "next request", {}, "start-again", "hash-again")


@pytest.mark.asyncio
async def test_model_view_carries_the_current_environment_within_the_estimate(
    tmp_path: Path, valid_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the workspace root, the platform and the tool limits the model works blind.

    Spec 7.2 and 7.3 item 1 make the current environment mandatory content, so it has to
    sit in the very system string the context estimator measures, not be appended later.
    """
    measured: list[str] = []
    original_build = ContextBuilder.build

    def recording_build(self, transcript, snapshot, request):
        measured.append(request.system)
        return original_build(self, transcript, snapshot, request)

    monkeypatch.setattr(ContextBuilder, "build", recording_build)
    loop, store, session_id, run_id, model, _, _ = _make_loop(
        tmp_path, valid_settings, [_final_turn()]
    )
    workspace_root = store.load_snapshot(session_id).session.workspace_realpath

    outcome = await loop.run(run_id, session_id, CancellationToken())

    assert outcome == RunOutcome.complete()
    system = measured[0]
    assert model.requests[0].system.startswith(system)
    assert workspace_root in system
    assert sys.platform in system
    assert str(valid_settings.tools.read_max_lines) in system
    assert str(valid_settings.tools.read_max_bytes) in system
    assert str(valid_settings.tools.command_timeout_seconds) in system
    assert str(valid_settings.tools.command_output_bytes) in system
    assert valid_settings.model.api_key_env not in system
    assert valid_settings.model.base_url not in system
    static_prompt = files("coding_agent.prompts").joinpath("system.md").read_text(encoding="utf-8")
    environment_cost = estimate_input_tokens(system, (), ()) - estimate_input_tokens(
        static_prompt, (), ()
    )
    assert 0 < environment_cost <= 200
