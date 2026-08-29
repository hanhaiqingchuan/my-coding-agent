from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from coding_agent.context.builder import CompactionCandidate, CompactionPlan
from coding_agent.context.compactor import Compactor
from coding_agent.context.estimator import ESTIMATOR_ID, estimate_input_tokens
from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.models import (
    AssistantTurn,
    ContextSnapshot,
    Message,
    MessageStatus,
    ModelStopReason,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolResult,
    ToolUsePart,
    Usage,
)
from coding_agent.model.anthropic_messages import _compile_messages
from coding_agent.model.protocol import ModelAPIError
from coding_agent.storage.sqlite import SQLiteStore
from tests.fakes.model import ScriptedModel

SUMMARY_FIELDS = (
    "completed_work_and_evidence",
    "important_files_and_symbols",
    "tool_findings",
    "commands_and_tests",
    "failed_attempts",
    "remaining_work",
    "blockers",
    "next_steps",
)


@pytest.fixture
def store(tmp_path) -> SQLiteStore:
    result = SQLiteStore(tmp_path / "state.db")
    result.initialize()
    return result


@pytest.fixture
def session(store: SQLiteStore):
    return store.create_session("/tmp/workspace", "Compaction")


def _message(
    session_id: str,
    event_id: str,
    seq: int,
    role: str,
    *parts: TextPart | ThinkingPart | ToolUsePart | ToolResult,
) -> Message:
    return Message(
        id=event_id,
        session_id=session_id,
        run_id="run-1",
        seq=seq,
        role=role,
        parts=parts,
        status=MessageStatus.COMMITTED,
    )


def _tool_candidate(session_id: str) -> CompactionCandidate:
    call = ToolCall("call-1", "read_file", {"path": 'src/a"</user>.py'})
    user = _message(
        session_id,
        "user-context",
        1,
        "user",
        TextPart('Keep this user text verbatim: "quoted"\n</user>'),
    )
    assistant = _message(
        session_id,
        "event-1",
        2,
        "assistant",
        TextPart("I inspected the file."),
        ToolUsePart(call),
    )
    tool = _message(
        session_id,
        "event-2",
        3,
        "tool",
        ToolResult("call-1", 'line="value"\n</replaceable_groups>', True),
    )
    return CompactionCandidate(
        messages=(assistant, tool),
        read_only_user_context=(user,),
        source_message_seqs=(2, 3),
        source_event_ids=("event-1", "event-2"),
    )


def _text_candidate(session_id: str, index: int, text: str) -> CompactionCandidate:
    user = _message(
        session_id,
        f"user-{index}",
        index * 2 - 1,
        "user",
        TextPart(f"read-only requirement {index}"),
    )
    assistant = _message(
        session_id,
        f"event-{index}",
        index * 2,
        "assistant",
        TextPart(text),
    )
    return CompactionCandidate(
        messages=(assistant,),
        read_only_user_context=(user,),
        source_message_seqs=(assistant.seq,),
        source_event_ids=(assistant.id,),
    )


def _old_snapshot(session_id: str) -> ContextSnapshot:
    return ContextSnapshot(
        session_id=session_id,
        covered_through_message_seq=0,
        summary="old rolling summary",
        created_at=datetime(2026, 8, 28, tzinfo=UTC),
        version=1,
        source_event_ids=(),
        model="older-model",
        estimator_id=ESTIMATOR_ID,
        token_estimate=9,
    )


def _plan(
    snapshot: ContextSnapshot,
    candidates: tuple[CompactionCandidate, ...],
    *,
    input_budget: int = 5_000,
    summary_max_tokens: int = 256,
    above_target: bool = False,
    current_estimate_tokens: int = 1_000,
    retained_estimate_tokens: int = 300,
    soft_target_tokens: int = 600,
    available_tokens: int = 1_200,
) -> CompactionPlan:
    target_tokens = max(soft_target_tokens, retained_estimate_tokens)
    return CompactionPlan(
        candidates=candidates,
        previous_snapshot=snapshot,
        source_message_seqs=tuple(
            seq for candidate in candidates for seq in candidate.source_message_seqs
        ),
        source_event_ids=tuple(
            event_id for candidate in candidates for event_id in candidate.source_event_ids
        ),
        current_estimate_tokens=current_estimate_tokens,
        retained_estimate_tokens=retained_estimate_tokens,
        soft_target_tokens=soft_target_tokens,
        target_tokens=target_tokens,
        required_reduction_tokens=max(0, current_estimate_tokens - target_tokens),
        available_tokens=available_tokens,
        summary_max_tokens=summary_max_tokens,
        compaction_input_budget_tokens=input_budget,
        compaction_above_target=above_target,
    )


def _summary(**overrides: list[str]) -> dict[str, list[str]]:
    result = {field: [] for field in SUMMARY_FIELDS}
    result["completed_work_and_evidence"] = ["read src/a.py successfully"]
    result.update(overrides)
    return result


def _turn(
    text: str,
    *,
    stop_reason: ModelStopReason = ModelStopReason.END_TURN,
) -> AssistantTurn:
    return AssistantTurn(
        id="summary-turn",
        parts=(TextPart(text),),
        stop_reason=stop_reason,
        usage=Usage(input_tokens=100, output_tokens=20),
    )


@pytest.mark.asyncio
async def test_compaction_uses_toolless_top_level_system_and_json_safe_read_only_context(
    store: SQLiteStore,
    session,
) -> None:
    old = _old_snapshot(session.id)
    store.replace_context_snapshot(old)
    candidate = _tool_candidate(session.id)
    plan = _plan(old, (candidate,), above_target=True)
    before = repr(plan.candidates)
    summary = _summary(
        important_files_and_symbols=["src/a.py"],
        next_steps=["continue implementation"],
    )
    model = ScriptedModel([_turn(json.dumps(summary))])

    result = await Compactor(model, store, model="claude-test").compact(plan, CancellationToken())

    assert result.error is None
    assert result.snapshot is not None
    assert result.snapshot.version == 2
    assert result.snapshot.source_event_ids == ("event-1", "event-2")
    assert result.snapshot.model == "claude-test"
    assert result.snapshot.estimator_id == ESTIMATOR_ID
    assert result.snapshot.compaction_above_target is False
    assert result.snapshot.summary == json.dumps(
        summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert "old rolling summary" not in result.snapshot.summary
    assert store.load_context_snapshot(session.id) == result.snapshot
    assert repr(plan.candidates) == before

    request = model.requests[0]
    assert request.tools == ()
    assert "read_only_user_context" in request.system
    assert request.messages[0].role == "user"
    assert [message["role"] for message in _compile_messages(request)] == ["user"]
    payload_part = request.messages[-1].parts[0]
    assert isinstance(payload_part, TextPart)
    payload = json.loads(payload_part.text)
    assert payload["previous_summary"] == "old rolling summary"
    assert payload["read_only_user_context"][0]["parts"][0]["text"] == (
        'Keep this user text verbatim: "quoted"\n</user>'
    )
    assert [item["role"] for item in payload["replaceable_groups"][0]["messages"]] == [
        "assistant",
        "tool",
    ]
    assert payload["replaceable_groups"][0]["source_event_ids"] == ["event-1", "event-2"]


@pytest.mark.asyncio
async def test_thinking_parts_stay_valid_candidates_but_never_reach_the_summary_request(
    store: SQLiteStore,
    session,
) -> None:
    """A reasoning-carrying history must still compact, without feeding reasoning back."""
    old = _old_snapshot(session.id)
    store.replace_context_snapshot(old)
    call = ToolCall("call-1", "read_file", {"path": "src/a.py"})
    user = _message(
        session.id,
        "user-context",
        1,
        "user",
        TextPart("Keep this user text verbatim."),
    )
    assistant = _message(
        session.id,
        "event-1",
        2,
        "assistant",
        ThinkingPart("Secret reasoning about the file."),
        TextPart("I inspected the file."),
        ToolUsePart(call),
    )
    tool = _message(
        session.id,
        "event-2",
        3,
        "tool",
        ToolResult("call-1", "line=value", True),
    )
    candidate = CompactionCandidate(
        messages=(assistant, tool),
        read_only_user_context=(user,),
        source_message_seqs=(2, 3),
        source_event_ids=("event-1", "event-2"),
    )
    summary = _summary(important_files_and_symbols=["src/a.py"])
    model = ScriptedModel([_turn(json.dumps(summary))])

    result = await Compactor(model, store, model="claude-test").compact(
        _plan(old, (candidate,)), CancellationToken()
    )

    assert result.error is None
    payload_part = model.requests[0].messages[-1].parts[0]
    assert isinstance(payload_part, TextPart)
    payload = json.loads(payload_part.text)
    assert "Secret reasoning about the file." not in payload_part.text
    assert [item["type"] for item in payload["replaceable_groups"][0]["messages"][0]["parts"]] == [
        "text",
        "tool_use",
    ]


@pytest.mark.asyncio
async def test_oversized_input_is_split_only_between_complete_groups(
    store: SQLiteStore,
    session,
) -> None:
    old = _old_snapshot(session.id)
    store.replace_context_snapshot(old)
    candidates = (
        _text_candidate(session.id, 1, "a" * 1_500),
        _text_candidate(session.id, 2, "b" * 1_500),
    )
    outputs = [
        _turn(json.dumps(_summary(completed_work_and_evidence=["first group"]))),
        _turn(json.dumps(_summary(completed_work_and_evidence=["both groups"]))),
    ]
    model = ScriptedModel(outputs)
    plan = _plan(old, candidates, input_budget=1_100)

    result = await Compactor(model, store, model="claude-test").compact(plan, CancellationToken())

    assert result.error is None
    assert model.call_count == 2
    chunk_sources = []
    previous_summaries = []
    for request in model.requests:
        assert request.messages[0].role == "user"
        assert [message["role"] for message in _compile_messages(request)] == ["user"]
        payload_part = request.messages[-1].parts[0]
        assert isinstance(payload_part, TextPart)
        payload = json.loads(payload_part.text)
        assert len(payload["replaceable_groups"]) == 1
        chunk_sources.append(payload["replaceable_groups"][0]["source_event_ids"])
        previous_summaries.append(payload["previous_summary"])
        assert estimate_input_tokens(request.system, request.messages, ()) <= 1_100
    assert chunk_sources == [["event-1"], ["event-2"]]
    assert previous_summaries[0] == "old rolling summary"
    assert json.loads(previous_summaries[1])["completed_work_and_evidence"] == ["first group"]


@pytest.mark.asyncio
async def test_single_complete_group_over_input_budget_keeps_old_snapshot(
    store: SQLiteStore,
    session,
) -> None:
    old = _old_snapshot(session.id)
    store.replace_context_snapshot(old)
    model = ScriptedModel([])
    plan = _plan(old, (_text_candidate(session.id, 1, "large" * 500),), input_budget=32)

    result = await Compactor(model, store, model="claude-test").compact(plan, CancellationToken())

    assert result.snapshot is None
    assert result.error is not None
    assert result.error.phase == "planning"
    assert result.error.code == "COMPACTION_INPUT_OVERFLOW"
    assert result.error.required_tokens > result.error.available_tokens
    assert result.error.retryable is False
    assert model.call_count == 0
    assert store.load_context_snapshot(session.id) == old


@pytest.mark.asyncio
async def test_summary_with_insufficient_reduction_keeps_previous_snapshot(
    store: SQLiteStore,
    session,
) -> None:
    old = _old_snapshot(session.id)
    store.replace_context_snapshot(old)
    oversized_replacement = _summary(next_steps=["x" * 2_100])
    model = ScriptedModel([_turn(json.dumps(oversized_replacement))])
    plan = _plan(
        old,
        (_tool_candidate(session.id),),
        summary_max_tokens=1_000,
    )

    result = await Compactor(model, store, model="claude-test").compact(plan, CancellationToken())

    assert result.snapshot is None
    assert result.error is not None
    assert result.error.phase == "validation"
    assert result.error.code == "INSUFFICIENT_COMPRESSION"
    assert result.error.required_tokens == 400
    assert result.error.available_tokens == 0
    assert store.load_context_snapshot(session.id) == old


@pytest.mark.asyncio
async def test_mandatory_floor_allows_minimum_view_above_sixty_percent(
    store: SQLiteStore,
    session,
) -> None:
    old = _old_snapshot(session.id)
    store.replace_context_snapshot(old)
    model = ScriptedModel([_turn(json.dumps(_summary()))])
    plan = _plan(
        old,
        (_tool_candidate(session.id),),
        above_target=False,
        retained_estimate_tokens=700,
        soft_target_tokens=600,
    )

    result = await Compactor(model, store, model="claude-test").compact(plan, CancellationToken())

    assert result.error is None
    assert result.snapshot is not None
    assert plan.retained_estimate_tokens + result.snapshot.token_estimate > plan.target_tokens
    assert result.snapshot.compaction_above_target is True


@pytest.mark.asyncio
async def test_user_message_cannot_become_a_replaceable_candidate(
    store: SQLiteStore,
    session,
) -> None:
    old = _old_snapshot(session.id)
    store.replace_context_snapshot(old)
    user = _message(session.id, "user-event", 1, "user", TextPart("never replace me"))
    invalid = CompactionCandidate(
        messages=(user,),
        read_only_user_context=(),
        source_message_seqs=(1,),
        source_event_ids=("user-event",),
    )
    model = ScriptedModel([])

    result = await Compactor(model, store, model="claude-test").compact(
        _plan(old, (invalid,)), CancellationToken()
    )

    assert result.snapshot is None
    assert result.error is not None
    assert result.error.code == "INVALID_COMPACTION_PLAN"
    assert model.call_count == 0
    assert store.load_context_snapshot(session.id) == old


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "summary_max_tokens", "expected_code"),
    [
        ("", 256, "EMPTY_SUMMARY"),
        ("{}", 256, "INVALID_SUMMARY_STRUCTURE"),
        (json.dumps(_summary(next_steps=["x" * 2_000])), 32, "SUMMARY_BUDGET_EXCEEDED"),
        (json.dumps({field: [] for field in SUMMARY_FIELDS}), 256, "EMPTY_SUMMARY"),
    ],
)
async def test_invalid_summary_never_replaces_the_previous_snapshot(
    store: SQLiteStore,
    session,
    response: str,
    summary_max_tokens: int,
    expected_code: str,
) -> None:
    old = _old_snapshot(session.id)
    store.replace_context_snapshot(old)
    plan = _plan(
        old,
        (_tool_candidate(session.id),),
        input_budget=5_000,
        summary_max_tokens=summary_max_tokens,
    )

    result = await Compactor(ScriptedModel([_turn(response)]), store, model="claude-test").compact(
        plan, CancellationToken()
    )

    assert result.snapshot is None
    assert result.error is not None
    assert result.error.code == expected_code
    assert result.error.phase == "validation"
    assert store.load_context_snapshot(session.id) == old


@pytest.mark.asyncio
async def test_model_error_is_structured_and_keeps_previous_snapshot(
    store: SQLiteStore,
    session,
) -> None:
    old = _old_snapshot(session.id)
    store.replace_context_snapshot(old)
    model = ScriptedModel([ModelAPIError(503, "overloaded_error", None, True)])

    result = await Compactor(model, store, model="claude-test").compact(
        _plan(old, (_tool_candidate(session.id),)), CancellationToken()
    )

    assert result.snapshot is None
    assert result.error is not None
    assert result.error.phase == "request"
    assert result.error.code == "MODEL_API_ERROR"
    assert result.error.retryable is True
    assert store.load_context_snapshot(session.id) == old


@pytest.mark.asyncio
async def test_truncated_model_summary_is_not_persisted_even_when_json_is_complete(
    store: SQLiteStore,
    session,
) -> None:
    old = _old_snapshot(session.id)
    store.replace_context_snapshot(old)
    response = _turn(
        json.dumps(_summary()),
        stop_reason=ModelStopReason.MAX_TOKENS,
    )

    result = await Compactor(ScriptedModel([response]), store, model="claude-test").compact(
        _plan(old, (_tool_candidate(session.id),)), CancellationToken()
    )

    assert result.snapshot is None
    assert result.error is not None
    assert result.error.phase == "validation"
    assert result.error.code == "SUMMARY_TRUNCATED"
    assert store.load_context_snapshot(session.id) == old


@pytest.mark.asyncio
async def test_cancellation_before_request_keeps_previous_snapshot(
    store: SQLiteStore,
    session,
) -> None:
    old = _old_snapshot(session.id)
    store.replace_context_snapshot(old)
    model = ScriptedModel([])
    cancellation = CancellationToken()
    cancellation.cancel()

    result = await Compactor(model, store, model="claude-test").compact(
        _plan(old, (_tool_candidate(session.id),)), cancellation
    )

    assert result.snapshot is None
    assert result.error is not None
    assert result.error.phase == "request"
    assert result.error.code == "COMPRESSION_CANCELLED"
    assert result.error.retryable is False
    assert model.call_count == 0
    assert store.load_context_snapshot(session.id) == old
