from __future__ import annotations

from datetime import UTC, datetime

import pytest

from coding_agent.context.builder import (
    CompactionRequired,
    ContextBuilder,
    ContextOverflow,
    ContextRequest,
    ReadyContext,
)
from coding_agent.core.models import (
    ContextSnapshot,
    Message,
    MessageStatus,
    TextPart,
    ToolCall,
    ToolResult,
    ToolUsePart,
)


def _message(
    seq: int,
    role: str,
    *parts: TextPart | ToolUsePart | ToolResult,
    run_id: str | None = None,
    status: MessageStatus = MessageStatus.COMMITTED,
    tool_call_id: str | None = None,
) -> Message:
    return Message(
        id=f"message-{seq}",
        session_id="session-1",
        run_id=run_id,
        seq=seq,
        role=role,
        parts=parts,
        status=status,
        tool_call_id=tool_call_id,
    )


def _request(
    *,
    context_window: int = 4_000,
    max_output_tokens: int = 200,
    safety_margin_tokens: int = 100,
    current_run_id: str = "run-4",
) -> ContextRequest:
    return ContextRequest(
        system="system instructions\nenvironment=/workspace",
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        safety_margin_tokens=safety_margin_tokens,
        compact_trigger_ratio=0.80,
        compact_target_ratio=0.60,
        summary_max_tokens=128,
        recent_user_turns=2,
        current_run_id=current_run_id,
        tool_schemas=(
            {
                "name": "read_file",
                "description": "Read a file",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        ),
    )


@pytest.fixture
def transcript() -> tuple[Message, ...]:
    old_call = ToolCall("call-old", "read_file", {"path": "old.py"})
    current_call = ToolCall("call-current", "read_file", {"path": "current.py"})
    pending_call = ToolCall("call-pending", "read_file", {"path": "pending.py"})
    return (
        _message(1, "user", TextPart("first\nexact"), run_id="run-1"),
        _message(2, "assistant", TextPart("first answer"), run_id="run-1"),
        _message(3, "user", TextPart("second 🧪"), run_id="run-2"),
        _message(4, "assistant", ToolUsePart(old_call), run_id="run-2"),
        _message(
            5,
            "tool",
            ToolResult("call-old", "x" * 1_200, True),
            run_id="run-2",
            tool_call_id="call-old",
        ),
        _message(6, "user", TextPart("third"), run_id="run-3"),
        _message(7, "assistant", TextPart("third answer"), run_id="run-3"),
        _message(8, "user", TextPart("current"), run_id="run-4"),
        _message(9, "assistant", ToolUsePart(current_call), run_id="run-4"),
        _message(
            10,
            "tool",
            ToolResult("call-current", "current result", True),
            run_id="run-4",
            tool_call_id="call-current",
        ),
        _message(
            11,
            "assistant",
            ToolUsePart(pending_call),
            run_id="run-4",
            status=MessageStatus.PENDING_TOOLS,
        ),
        _message(
            12,
            "assistant",
            TextPart("interrupted draft"),
            run_id="run-4",
            status=MessageStatus.INTERRUPTED,
        ),
    )


def _text_user_messages(result: ReadyContext | CompactionRequired) -> list[str]:
    return [
        part.text
        for message in result.view.messages
        if message.role == "user"
        for part in message.parts
        if isinstance(part, TextPart)
    ]


def _all_text(result: ReadyContext | CompactionRequired) -> str:
    return "\n".join(
        part.text if isinstance(part, TextPart) else part.content
        for message in result.view.messages
        for part in message.parts
        if isinstance(part, TextPart | ToolResult)
    )


def test_projection_preserves_each_committed_user_verbatim_once_and_is_pure(
    transcript: tuple[Message, ...],
) -> None:
    before = repr(transcript)
    snapshot = ContextSnapshot(
        session_id="session-1",
        covered_through_message_seq=5,
        summary="Earlier assistant work was summarized.",
        created_at=datetime(2026, 8, 28, tzinfo=UTC),
    )

    result = ContextBuilder().build(transcript, snapshot, _request())

    assert isinstance(result, ReadyContext)
    assert _text_user_messages(result) == ["first\nexact", "second 🧪", "third", "current"]
    assert repr(transcript) == before
    assert "pending.py" not in _all_text(result)
    assert "interrupted draft" not in _all_text(result)
    assert "Earlier assistant work was summarized." in _all_text(result)


def test_recent_two_turns_and_current_committed_tool_exchange_remain_complete(
    transcript: tuple[Message, ...],
) -> None:
    snapshot = ContextSnapshot(
        session_id="session-1",
        covered_through_message_seq=7,
        summary="summary",
        created_at=datetime(2026, 8, 28, tzinfo=UTC),
    )

    result = ContextBuilder().build(transcript, snapshot, _request())

    assert isinstance(result, ReadyContext)
    assert "third answer" in _all_text(result)
    call_message_index = next(
        index
        for index, message in enumerate(result.view.messages)
        if any(
            isinstance(part, ToolUsePart) and part.call.id == "call-current"
            for part in message.parts
        )
    )
    result_message = result.view.messages[call_message_index + 1]
    assert result_message.role == "user"
    assert [part.tool_call_id for part in result_message.parts if isinstance(part, ToolResult)] == [
        "call-current"
    ]


def test_active_user_does_not_consume_a_completed_recent_round_slot() -> None:
    transcript = (
        _message(1, "user", TextPart("round 1"), run_id="run-1"),
        _message(2, "assistant", TextPart("round 1 answer"), run_id="run-1"),
        _message(3, "user", TextPart("round 2"), run_id="run-2"),
        _message(4, "assistant", TextPart("round 2 answer"), run_id="run-2"),
        _message(5, "user", TextPart("round 3"), run_id="run-3"),
        _message(6, "assistant", TextPart("round 3 answer"), run_id="run-3"),
        _message(7, "user", TextPart("active request"), run_id="run-active"),
    )
    snapshot = ContextSnapshot(
        session_id="session-1",
        covered_through_message_seq=7,
        summary="older assistant work",
        created_at=datetime(2026, 8, 28, tzinfo=UTC),
    )

    result = ContextBuilder().build(
        transcript,
        snapshot,
        _request(current_run_id="run-active"),
    )

    assert isinstance(result, ReadyContext)
    assert "round 1 answer" not in _all_text(result)
    assert "round 2 answer" in _all_text(result)
    assert "round 3 answer" in _all_text(result)
    assert _text_user_messages(result) == [
        "round 1",
        "round 2",
        "round 3",
        "active request",
    ]


def test_snapshot_covered_current_run_tool_exchanges_are_not_mandatory() -> None:
    covered_first = ToolCall("call-covered-1", "read_file", {"path": "first.py"})
    covered_second = ToolCall("call-covered-2", "read_file", {"path": "second.py"})
    after_snapshot = ToolCall("call-after-snapshot", "read_file", {"path": "latest.py"})
    transcript = (
        _message(1, "user", TextPart("round 1"), run_id="run-1"),
        _message(2, "assistant", TextPart("round 1 answer"), run_id="run-1"),
        _message(3, "user", TextPart("round 2"), run_id="run-2"),
        _message(4, "assistant", TextPart("round 2 answer"), run_id="run-2"),
        _message(5, "user", TextPart("round 3"), run_id="run-3"),
        _message(6, "assistant", TextPart("round 3 answer"), run_id="run-3"),
        _message(7, "user", TextPart("active request"), run_id="run-active"),
        _message(8, "assistant", ToolUsePart(covered_first), run_id="run-active"),
        _message(
            9,
            "tool",
            ToolResult("call-covered-1", "first result", True),
            run_id="run-active",
            tool_call_id="call-covered-1",
        ),
        _message(10, "assistant", ToolUsePart(covered_second), run_id="run-active"),
        _message(
            11,
            "tool",
            ToolResult("call-covered-2", "second result", True),
            run_id="run-active",
            tool_call_id="call-covered-2",
        ),
        _message(12, "assistant", ToolUsePart(after_snapshot), run_id="run-active"),
        _message(
            13,
            "tool",
            ToolResult("call-after-snapshot", "latest result", True),
            run_id="run-active",
            tool_call_id="call-after-snapshot",
        ),
    )
    snapshot = ContextSnapshot(
        session_id="session-1",
        covered_through_message_seq=11,
        summary="summary through the second current-run exchange",
        created_at=datetime(2026, 8, 28, tzinfo=UTC),
    )

    result = ContextBuilder().build(
        transcript,
        snapshot,
        _request(current_run_id="run-active"),
    )

    assert isinstance(result, ReadyContext)
    projected_call_ids = [
        part.call.id
        for message in result.view.messages
        for part in message.parts
        if isinstance(part, ToolUsePart)
    ]
    projected_result_ids = [
        part.tool_call_id
        for message in result.view.messages
        for part in message.parts
        if isinstance(part, ToolResult)
    ]
    assert projected_call_ids == ["call-after-snapshot"]
    assert projected_result_ids == ["call-after-snapshot"]


def test_mandatory_content_overflow_prevents_model_call() -> None:
    transcript = (_message(1, "user", TextPart("不可截断" * 300), run_id="run-1"),)

    result = ContextBuilder().build(
        transcript,
        None,
        _request(context_window=400, max_output_tokens=100, safety_margin_tokens=100),
    )

    assert isinstance(result, ContextOverflow)
    assert result.code == "CONTEXT_OVERFLOW"
    assert result.mandatory_user_tokens > result.available_tokens
    assert result.required_tokens == result.mandatory_tokens
    assert result.diagnostic["reason"] == "mandatory_content_exceeds_available_input"


def test_old_tool_output_is_pruned_before_summary() -> None:
    old_call = ToolCall("call-old", "read_file", {"path": "old.py"})
    transcript = (
        _message(1, "user", TextPart("first"), run_id="run-1"),
        _message(2, "assistant", ToolUsePart(old_call), run_id="run-1"),
        _message(
            3,
            "tool",
            ToolResult("call-old", "x" * 1_200, True),
            run_id="run-1",
            tool_call_id="call-old",
        ),
        _message(4, "user", TextPart("second"), run_id="run-2"),
        _message(5, "assistant", TextPart("second answer"), run_id="run-2"),
        _message(6, "user", TextPart("third"), run_id="run-3"),
        _message(7, "assistant", TextPart("third answer"), run_id="run-3"),
        _message(8, "user", TextPart("current"), run_id="run-4"),
    )
    result = ContextBuilder().build(
        transcript,
        None,
        _request(context_window=850, max_output_tokens=100, safety_margin_tokens=100),
    )

    assert isinstance(result, ReadyContext | CompactionRequired)
    assert result.pruned_bytes > 0
    serialized = _all_text(result)
    assert "tool=read_file" in serialized
    assert "status=succeeded" in serialized
    assert "target=old.py" in serialized
    assert "original_bytes=1200" in serialized
    assert "original_truncated=false" in serialized
    assert "context_pruned=true" in serialized


def test_pruning_preserves_small_results_inside_an_atomic_multi_tool_group() -> None:
    large_call = ToolCall("call-large", "read_file", {"path": "large.py"})
    small_call = ToolCall("call-small", "read_file", {"path": "small.py"})
    transcript = (
        _message(1, "user", TextPart("old"), run_id="run-1"),
        _message(
            2,
            "assistant",
            ToolUsePart(large_call),
            ToolUsePart(small_call),
            run_id="run-1",
        ),
        _message(
            3,
            "tool",
            ToolResult("call-large", "x" * 1_500, True),
            run_id="run-1",
            tool_call_id="call-large",
        ),
        _message(
            4,
            "tool",
            ToolResult("call-small", "keep this exact", True),
            run_id="run-1",
            tool_call_id="call-small",
        ),
        _message(5, "user", TextPart("second"), run_id="run-2"),
        _message(6, "assistant", TextPart("second answer"), run_id="run-2"),
        _message(7, "user", TextPart("third"), run_id="run-3"),
        _message(8, "assistant", TextPart("third answer"), run_id="run-3"),
        _message(9, "user", TextPart("current"), run_id="run-4"),
    )

    result = ContextBuilder().build(
        transcript,
        None,
        _request(
            context_window=950,
            max_output_tokens=100,
            safety_margin_tokens=100,
            current_run_id="run-4",
        ),
    )

    assert isinstance(result, ReadyContext | CompactionRequired)
    assert result.pruned_bytes > 0
    tool_results = [
        part
        for message in result.view.messages
        for part in message.parts
        if isinstance(part, ToolResult)
    ]
    assert [result.tool_call_id for result in tool_results] == ["call-large", "call-small"]
    assert tool_results[1].content == "keep this exact"
    assert tool_results[1].truncated is False


def test_compaction_plan_uses_complete_replaceable_groups() -> None:
    old_call = ToolCall("call-old", "read_file", {"path": "old.py"})
    transcript = (
        _message(1, "user", TextPart("first"), run_id="run-1"),
        _message(
            2,
            "assistant",
            TextPart("analysis " * 500),
            ToolUsePart(old_call),
            run_id="run-1",
        ),
        _message(
            3,
            "tool",
            ToolResult("call-old", "x" * 1_200, True),
            run_id="run-1",
            tool_call_id="call-old",
        ),
        _message(4, "user", TextPart("second"), run_id="run-2"),
        _message(5, "assistant", TextPart("second answer"), run_id="run-2"),
        _message(6, "user", TextPart("third"), run_id="run-3"),
        _message(7, "assistant", TextPart("third answer"), run_id="run-3"),
        _message(8, "user", TextPart("current"), run_id="run-4"),
    )

    result = ContextBuilder().build(
        transcript,
        None,
        _request(context_window=1_250, max_output_tokens=100, safety_margin_tokens=100),
    )

    assert isinstance(result, CompactionRequired)
    old_tool_candidate = next(
        candidate for candidate in result.plan.candidates if candidate.source_message_seqs == (2, 3)
    )
    assert [message.role for message in old_tool_candidate.messages] == ["assistant", "tool"]
    assert [message.seq for message in old_tool_candidate.read_only_user_context] == [1]
    assert old_tool_candidate.source_event_ids == ("message-2", "message-3")
    assert result.plan.source_event_ids == ("message-2", "message-3")
    assert result.plan.required_reduction_tokens > 0


def test_eighty_percent_trigger_uses_sixty_percent_soft_target_with_mandatory_floor() -> None:
    transcript = (
        _message(1, "user", TextPart("u" * 1_200), run_id="run-1"),
        _message(2, "assistant", TextPart("a" * 500), run_id="run-1"),
        _message(3, "user", TextPart("next"), run_id="run-2"),
        _message(4, "assistant", TextPart("next answer"), run_id="run-2"),
        _message(5, "user", TextPart("more"), run_id="run-3"),
        _message(6, "assistant", TextPart("more answer"), run_id="run-3"),
        _message(7, "user", TextPart("latest"), run_id="run-4"),
    )
    request = _request(
        context_window=900,
        max_output_tokens=100,
        safety_margin_tokens=100,
        current_run_id="run-4",
    )

    result = ContextBuilder().build(transcript, None, request)

    assert isinstance(result, CompactionRequired)
    assert result.estimated_tokens >= result.trigger_tokens
    assert result.mandatory_tokens > int(result.available_tokens * 0.60)
    assert result.target_tokens == result.mandatory_tokens
    assert result.plan.retained_estimate_tokens == result.mandatory_tokens
    assert result.plan.soft_target_tokens == 420
    assert result.plan.compaction_above_target is True


def _all_mandatory_above_target_transcript() -> tuple[Message, ...]:
    return (
        _message(1, "user", TextPart("u" * 3_000), run_id="run-1"),
        _message(2, "assistant", TextPart("old answer"), run_id="run-1"),
        _message(3, "user", TextPart("second request"), run_id="run-2"),
        _message(4, "assistant", TextPart("second answer"), run_id="run-2"),
        _message(5, "user", TextPart("third request"), run_id="run-3"),
        _message(6, "assistant", TextPart("third answer"), run_id="run-3"),
        _message(7, "user", TextPart("latest request"), run_id="run-4"),
    )


def _covering_snapshot(summary: str) -> ContextSnapshot:
    return ContextSnapshot(
        session_id="session-1",
        covered_through_message_seq=2,
        summary=summary,
        created_at=datetime(2026, 8, 28, tzinfo=UTC),
    )


def test_minimal_legal_view_above_target_is_ready_when_it_fits_available_budget() -> None:
    """A view with nothing left to compact must not be reported as a compaction request.

    Spec 7.4 item 10 makes 60% a soft target: once every visible group is mandatory and
    only the rolling summary pushes the estimate past ``target_tokens``, the minimal legal
    view still fits the available budget and must be usable.
    """
    result = ContextBuilder().build(
        _all_mandatory_above_target_transcript(),
        _covering_snapshot("s" * 900),
        _request(context_window=2_000, max_output_tokens=200, safety_margin_tokens=100),
    )

    assert isinstance(result, ReadyContext)
    assert result.estimated_tokens > result.target_tokens
    assert result.estimated_tokens >= result.trigger_tokens
    assert result.estimated_tokens <= result.available_tokens
    assert result.compaction_above_target is True
    assert _text_user_messages(result) == [
        "u" * 3_000,
        "second request",
        "third request",
        "latest request",
    ]
    assert "s" * 900 in _all_text(result)


def test_minimal_legal_view_overflows_only_above_the_available_budget() -> None:
    """Reserving CONTEXT_OVERFLOW for estimates within the budget would kill healthy runs."""
    result = ContextBuilder().build(
        _all_mandatory_above_target_transcript(),
        _covering_snapshot("s" * 3_000),
        _request(context_window=2_000, max_output_tokens=200, safety_margin_tokens=100),
    )

    assert isinstance(result, ContextOverflow)
    assert result.code == "CONTEXT_OVERFLOW"
    assert result.mandatory_tokens <= result.available_tokens
    assert result.required_tokens > result.available_tokens
    assert result.diagnostic["reason"] == "minimal_view_exceeds_available_input"


def test_exactly_eighty_percent_triggers_compaction() -> None:
    transcript = (
        _message(1, "user", TextPart("1"), run_id="run-1"),
        _message(2, "assistant", TextPart("a"), run_id="run-1"),
        _message(3, "user", TextPart("2"), run_id="run-2"),
        _message(4, "assistant", TextPart("b"), run_id="run-2"),
        _message(5, "user", TextPart("3"), run_id="run-3"),
        _message(6, "assistant", TextPart("c"), run_id="run-3"),
        _message(7, "user", TextPart("4"), run_id="run-4"),
    )
    request = ContextRequest(
        system="",
        context_window=191,
        max_output_tokens=10,
        safety_margin_tokens=10,
        compact_trigger_ratio=0.80,
        compact_target_ratio=0.60,
        summary_max_tokens=16,
        recent_user_turns=2,
        current_run_id="run-4",
    )

    result = ContextBuilder().build(transcript, None, request)

    assert isinstance(result, CompactionRequired)
    assert result.estimated_tokens == result.trigger_tokens == 137


def test_request_rejects_an_invalid_budget_before_building_context() -> None:
    with pytest.raises(ValueError, match="context window must exceed"):
        _request(context_window=200, max_output_tokens=100, safety_margin_tokens=100)
