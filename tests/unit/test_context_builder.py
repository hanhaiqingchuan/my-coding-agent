from __future__ import annotations

from dataclasses import replace
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
    ThinkingPart,
    ToolCall,
    ToolResult,
    ToolUsePart,
)


def _message(
    seq: int,
    role: str,
    *parts: TextPart | ThinkingPart | ToolUsePart | ToolResult,
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


def test_committed_thinking_parts_never_enter_the_model_view(
    transcript: tuple[Message, ...],
) -> None:
    """Reasoning is display-only; the provider must never see it again as context."""
    thinking_transcript = (
        _message(1, "user", TextPart("first\nexact"), run_id="run-1"),
        _message(
            2,
            "assistant",
            ThinkingPart("I wonder about the answer."),
            TextPart("first answer"),
            run_id="run-1",
        ),
        _message(3, "user", TextPart("second 🧪"), run_id="run-2"),
        _message(
            4,
            "assistant",
            ThinkingPart("Deeper reasoning this round."),
            ToolUsePart(ToolCall("call-old", "read_file", {"path": "old.py"})),
            run_id="run-2",
        ),
        _message(
            5,
            "tool",
            ToolResult("call-old", "x" * 1_200, True),
            run_id="run-2",
            tool_call_id="call-old",
        ),
        _message(6, "user", TextPart("third"), run_id="run-3"),
        _message(7, "assistant", TextPart("third answer"), run_id="run-3"),
    )

    result = ContextBuilder().build(thinking_transcript, None, _request())

    assert isinstance(result, ReadyContext)
    assert all(
        not isinstance(part, ThinkingPart)
        for message in result.view.messages
        for part in message.parts
    )
    assert "I wonder about the answer." not in _all_text(result)
    assert "Deeper reasoning this round." not in _all_text(result)
    assert "first answer" in _all_text(result)


def test_mandatory_content_accounting_ignores_thinking_parts() -> None:
    """Thinking must not change which groups are mandatory or the mandatory token floor."""
    call = ToolCall("call-mandatory", "read_file", {"path": "current.py"})
    with_thinking = (
        _message(1, "user", TextPart("current"), run_id="run-active"),
        _message(
            2,
            "assistant",
            ThinkingPart("reasoning before the call"),
            ToolUsePart(call),
            run_id="run-active",
        ),
        _message(
            3,
            "tool",
            ToolResult("call-mandatory", "current result", True),
            run_id="run-active",
            tool_call_id="call-mandatory",
        ),
    )
    without_thinking = (
        with_thinking[0],
        _message(2, "assistant", ToolUsePart(call), run_id="run-active"),
        with_thinking[2],
    )
    request = _request()

    with_result = ContextBuilder().build(with_thinking, None, request)
    without_result = ContextBuilder().build(without_thinking, None, request)

    assert isinstance(with_result, ReadyContext)
    assert isinstance(without_result, ReadyContext)
    assert with_result.view.messages == without_result.view.messages
    assert with_result.mandatory_tokens == without_result.mandatory_tokens
    assert with_result.mandatory_user_tokens == without_result.mandatory_user_tokens
    assert with_result.estimated_tokens == without_result.estimated_tokens


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


def test_real_session_scale_thinking_heavy_transcript_builds_without_overflow() -> None:
    """Regression proof from session df0e628d (run 960e5652, CONTEXT_OVERFLOW).

    The real transcript held ~74 KiB of display-only ThinkingPart (~24k tokens if
    it were counted) next to ~108 KiB of genuine tool_use arguments, on a 64k
    window (available = 64k - 8k output - 2k margin = 53,760). Thinking is
    display-only by design (spec 7.4, 8.2): the model view never carries it, so
    no accounting path may count it either. A synthetic transcript sized like the
    real one must therefore complete the build instead of overflowing, and the
    accounting must be identical to the same transcript with thinking stripped.
    """
    transcript = _real_session_scale_transcript()
    request = _real_session_scale_request()

    result = ContextBuilder().build(transcript, None, request)

    assert not isinstance(result, ContextOverflow)
    assert isinstance(result, ReadyContext | CompactionRequired)
    assert result.estimated_tokens <= result.available_tokens

    stripped = tuple(
        replace(
            message,
            parts=tuple(part for part in message.parts if not isinstance(part, ThinkingPart)),
        )
        for message in transcript
    )
    stripped_result = ContextBuilder().build(stripped, None, request)

    assert type(stripped_result) is type(result)
    assert stripped_result.estimated_tokens == result.estimated_tokens
    assert stripped_result.mandatory_tokens == result.mandatory_tokens
    assert stripped_result.available_tokens == result.available_tokens


def _real_session_scale_transcript() -> tuple[Message, ...]:
    """Shape and size the failing session: 3 runs, ~46 thinking parts, 49 tool calls.

    The committed history mirrors df0e628d: one short first run, one long completed
    run of read/plan exchanges, and the current run whose rounds are all mandatory.
    Byte targets: 74 KiB of thinking text, ~108 KiB of tool_use input payloads and
    ~22 KiB of tool-result content, all UTF-8 (CJK characters are 3 bytes each).
    """
    thinking_per_turn = (74 * 1_024) // 46
    args_per_call = (108 * 1_024) // 49
    result_per_call = (22 * 1_024) // 49

    messages: list[Message] = []
    seq = 0

    def add(role: str, *parts: object, run_id: str, tool_call_id: str | None = None) -> None:
        nonlocal seq
        seq += 1
        messages.append(
            _message(  # type: ignore[arg-type]
                seq,
                role,
                *parts,
                run_id=run_id,
                tool_call_id=tool_call_id,
            )
        )

    add("user", TextPart("你好，你是谁"), run_id="run-a")
    add("assistant", TextPart("我是你的编码助手。"), run_id="run-a")

    add("user", TextPart("帮我查看项目结构并整理 tmp 目录"), run_id="run-b")
    for index in range(24):
        call_id = f"call-b-{index}"
        args = {
            "command": "ls -la",
            "reason": "查看目录结构 " + "条目" * ((args_per_call - 120) // 6),
        }
        add(
            "assistant",
            ThinkingPart("思考" * (thinking_per_turn // 6)),
            TextPart(f"第{index + 1}步：先查看目录。"),
            ToolUsePart(ToolCall(call_id, "run_command", args)),
            run_id="run-b",
        )
        add(
            "tool",
            ToolResult(
                call_id,
                "总用量 4\ndrwxr-xr-x tmp\n" + "行数据" * (result_per_call // 9),
                True,
            ),
            run_id="run-b",
            tool_call_id=call_id,
        )
    add("assistant", TextPart("目录已确认，继续下一步。"), run_id="run-b")

    add("user", TextPart("继续整理，把临时文件归类"), run_id="run-c")
    for index in range(25):
        call_id = f"call-c-{index}"
        args = {
            "path": "tmp/notes.txt",
            "reason": "读取文件内容 " + "片段" * ((args_per_call - 120) // 6),
        }
        add(
            "assistant",
            ThinkingPart("推理" * (thinking_per_turn // 6)),
            TextPart(f"读取第{index + 1}个文件。"),
            ToolUsePart(ToolCall(call_id, "read_file", args)),
            run_id="run-c",
        )
        add(
            "tool",
            ToolResult(call_id, "第一行内容\n第二行内容\n" + "数据" * (result_per_call // 9), True),
            run_id="run-c",
            tool_call_id=call_id,
        )
    return tuple(messages)


def _real_session_scale_request() -> ContextRequest:
    """The failing run's configured budget: 64k window, 8k output, 2k margin."""
    return ContextRequest(
        system=(
            "system instructions\n\nCurrent environment:\n"
            "- workspace root: /Users/hhc/Desktop/codes/my-agent/tmp\n"
            "- platform: darwin\n"
            "- read_file returns at most 800 lines or 40960 bytes per call\n"
            "- run_command times out after 120s and truncates output at 40960 bytes"
        ),
        context_window=64_000,
        max_output_tokens=8_192,
        safety_margin_tokens=2_048,
        compact_trigger_ratio=0.80,
        compact_target_ratio=0.60,
        summary_max_tokens=2_048,
        recent_user_turns=2,
        current_run_id="run-c",
        tool_schemas=(
            {
                "name": "read_file",
                "description": "Read a UTF-8 text file with line and byte limits",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "offset": {"type": "integer"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "write_file",
                "description": "Write or replace a UTF-8 text file after approval",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string"},
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["operation", "path"],
                },
            },
            {
                "name": "run_command",
                "description": "Run a non-interactive shell command after approval",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "reason": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "skill",
                "description": "Read or list on-demand workspace skills",
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "mode": {"type": "string"}},
                },
            },
        ),
    )


def test_request_rejects_an_invalid_budget_before_building_context() -> None:
    with pytest.raises(ValueError, match="context window must exceed"):
        _request(context_window=200, max_output_tokens=100, safety_margin_tokens=100)


def test_system_prompt_with_workspace_instructions_survives_pruning_and_compaction() -> None:
    """Spec 7.2 makes the compiled system (AGENTS.md included) non-compressible content.

    The workspace instructions ride inside ``request.system``, so pruning old tool
    output and planning a summary must never touch them: every view this builder
    returns carries the system verbatim, and the pruned placeholders replace only
    old tool results.
    """
    old_call = ToolCall("call-old", "read_file", {"path": "old.py"})
    transcript = (
        _message(1, "user", TextPart("first"), run_id="run-1"),
        _message(2, "assistant", ToolUsePart(old_call), run_id="run-1"),
        _message(
            3,
            "tool",
            ToolResult("call-old", "x" * 2_000, True),
            run_id="run-1",
            tool_call_id="call-old",
        ),
        _message(4, "user", TextPart("second"), run_id="run-2"),
        _message(5, "assistant", TextPart("second answer"), run_id="run-2"),
        _message(6, "user", TextPart("third"), run_id="run-3"),
        _message(7, "assistant", TextPart("third answer"), run_id="run-3"),
        _message(8, "user", TextPart("current"), run_id="run-4"),
    )
    instructions = "所有回复以'收到'开头\n" + "细则 " * 100
    request = replace(
        _request(context_window=1_200, max_output_tokens=100, safety_margin_tokens=100),
        system=(
            "system instructions\nenvironment=/workspace\n\n"
            "## Workspace instructions (AGENTS.md)\n\n" + instructions
        ),
    )

    result = ContextBuilder().build(transcript, None, request)

    assert isinstance(result, ReadyContext | CompactionRequired)
    assert result.view.system == request.system
    assert instructions in result.view.system
    assert result.pruned_bytes > 0
    serialized = _all_text(result)
    assert "context_pruned=true" in serialized
    assert "x" * 200 not in serialized
