from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from coding_agent.core.models import (
    AssistantTurn,
    ModelStopReason,
    RunTotals,
    TextPart,
    ToolCall,
    ToolError,
    ToolResult,
    ToolUsePart,
    Usage,
)


def test_run_totals_reject_a_negative_counter() -> None:
    """The run aggregate only ever holds monotonic SQLite sums, never a corrupted count."""
    with pytest.raises(ValueError, match="round_count"):
        RunTotals(round_count=-1)


def test_tool_result_rejects_success_with_error() -> None:
    """Dropping this check would let successful tool responses be sent as wire errors."""
    with pytest.raises(ValueError, match="successful"):
        ToolResult(
            tool_call_id="call-1",
            content="read 3 lines",
            ok=True,
            error=ToolError(code="read_failed", message="unexpected"),
        )


def test_tool_result_rejects_failure_without_stable_error_code() -> None:
    """Removing the code requirement would force later layers to parse error prose."""
    with pytest.raises(ValueError, match="error"):
        ToolResult(tool_call_id="call-1", content="", ok=False)


def test_assistant_turn_preserves_text_and_tool_use_order() -> None:
    """Reordering blocks would change the model-visible meaning of an assistant turn."""
    first_call = ToolCall(id="call-1", name="read_file", input={"path": "a.py"})
    second_call = ToolCall(id="call-2", name="read_file", input={"path": "b.py"})
    parts = (
        TextPart("I will inspect both files."),
        ToolUsePart(first_call),
        TextPart("Then I will compare them."),
        ToolUsePart(second_call),
    )

    turn = AssistantTurn(
        id="assistant-1",
        parts=parts,
        stop_reason=ModelStopReason.TOOL_USE,
        usage=Usage(input_tokens=10, output_tokens=5),
    )

    assert turn.parts == parts


def test_assistant_turn_rejects_duplicate_tool_call_ids() -> None:
    """Allowing duplicate IDs would make tool results impossible to pair unambiguously."""
    duplicate_call = ToolCall(id="call-1", name="read_file", input={"path": "a.py"})

    with pytest.raises(ValueError, match="unique"):
        AssistantTurn(
            id="assistant-1",
            parts=(ToolUsePart(duplicate_call), ToolUsePart(duplicate_call)),
            stop_reason=ModelStopReason.TOOL_USE,
            usage=Usage(),
        )


@pytest.mark.parametrize(
    "input_value",
    [
        {"payload": bytearray(b"mutable")},
        {"nested": {"unsupported": {"mutable"}}},
    ],
)
def test_tool_call_rejects_unsupported_json_values_recursively(input_value: object) -> None:
    """Accepting mutable non-JSON values would let callers mutate a supposedly frozen DTO."""
    with pytest.raises(TypeError, match="JSON"):
        ToolCall(id="call-1", name="read_file", input=input_value)  # type: ignore[arg-type]


def test_dto_is_frozen_and_rejects_unknown_fields() -> None:
    """Making DTOs mutable or permissive would let one layer silently corrupt another."""
    part = TextPart("immutable")

    with pytest.raises(FrozenInstanceError):
        part.text = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError, match="unexpected"):
        TextPart(text="known", extra="not allowed")  # type: ignore[call-arg]
