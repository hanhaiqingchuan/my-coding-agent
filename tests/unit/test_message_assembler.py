from __future__ import annotations

import pytest

from coding_agent.core.models import ModelStopReason, TextPart, Usage
from coding_agent.model.message_assembler import MessageStreamAssembler, NormalizedMessageEvent
from coding_agent.model.protocol import ModelProtocolError, ModelTransportError, TextDelta
from tests.fixtures.anthropic_events import text_response_events, two_tool_use_event_stream


def normalized(raw: dict[str, object]) -> NormalizedMessageEvent:
    return NormalizedMessageEvent.from_mapping(raw)


def feed_all(assembler: MessageStreamAssembler, events: list[dict[str, object]]) -> list[TextDelta]:
    deltas: list[TextDelta] = []
    for event in events:
        deltas.extend(assembler.feed(normalized(event)))
    return deltas


def test_tool_inputs_and_parts_are_assembled_by_content_block_index() -> None:
    """Mixing block slots would execute arguments under the wrong tool name."""
    assembler = MessageStreamAssembler()

    deltas = feed_all(assembler, list(two_tool_use_event_stream()))
    turn = assembler.finish()

    assert deltas == [TextDelta(index=0, text="Checking files.")]
    assert isinstance(turn.parts[0], TextPart)
    assert [call.name for call in turn.tool_calls] == ["read_file", "run_command"]
    assert dict(turn.tool_calls[0].input) == {"path": "a.py"}
    assert dict(turn.tool_calls[1].input) == {"command": "pytest"}
    assert turn.stop_reason is ModelStopReason.TOOL_USE
    assert turn.usage == Usage(
        input_tokens=21,
        output_tokens=9,
        cache_creation_input_tokens=5,
        cache_read_input_tokens=8,
    )


@pytest.mark.parametrize(
    ("wire_reason", "expected"),
    [
        ("end_turn", ModelStopReason.END_TURN),
        ("max_tokens", ModelStopReason.MAX_TOKENS),
        ("stop_sequence", ModelStopReason.STOP_SEQUENCE),
        ("pause_turn", ModelStopReason.PAUSE_TURN),
        ("refusal", ModelStopReason.REFUSAL),
        (
            "model_context_window_exceeded",
            ModelStopReason.MODEL_CONTEXT_WINDOW_EXCEEDED,
        ),
    ],
)
def test_supported_stop_reasons_remain_distinguishable(
    wire_reason: str, expected: ModelStopReason
) -> None:
    """Collapsing special stop reasons would send the Agent Loop down the wrong branch."""
    assembler = MessageStreamAssembler()
    feed_all(assembler, text_response_events(stop_reason=wire_reason))

    assert assembler.finish().stop_reason is expected


def test_usage_is_replaced_by_final_cumulative_values() -> None:
    """Adding message_start and message_delta usage would double-count the request."""
    assembler = MessageStreamAssembler()
    feed_all(assembler, text_response_events())

    assert assembler.finish().usage == Usage(
        input_tokens=13,
        output_tokens=5,
        cache_creation_input_tokens=6,
        cache_read_input_tokens=7,
    )


def test_usage_rejects_non_integer_token_counts() -> None:
    """Accepting fractional token counts would break persisted usage accounting."""
    with pytest.raises(ValueError, match="non-negative integer"):
        Usage(input_tokens=1.5)  # type: ignore[arg-type]


def test_malformed_provider_usage_is_a_protocol_error() -> None:
    """Treating a malformed count as null would hide a provider protocol violation."""
    event = text_response_events()[0]
    message = event["message"]
    assert isinstance(message, dict)
    usage = message["usage"]
    assert isinstance(usage, dict)
    usage["input_tokens"] = "12"

    with pytest.raises(ModelProtocolError) as raised:
        normalized(event)

    assert raised.value.code == "INVALID_USAGE"


@pytest.mark.parametrize("event_index", [0, -2])
def test_usage_events_require_provider_usage_object(event_index: int) -> None:
    """Silently inventing absent provider usage would make missing protocol data look real."""
    events = text_response_events()
    event = events[event_index]
    if event["type"] == "message_start":
        message = event["message"]
        assert isinstance(message, dict)
        message.pop("usage")
        events = [event]
    else:
        event.pop("usage")
        events = events[:event_index]
        events.append(event)
    assembler = MessageStreamAssembler()

    with pytest.raises(ModelProtocolError) as raised:
        feed_all(assembler, events)

    assert raised.value.code == "MISSING_MESSAGE_USAGE"


@pytest.mark.parametrize(
    ("events", "code"),
    [
        (
            [
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                }
            ],
            "MESSAGE_NOT_STARTED",
        ),
        (
            [
                text_response_events()[0],
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "late"},
                },
            ],
            "BLOCK_NOT_STARTED",
        ),
        (
            [
                text_response_events()[0],
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "text", "text": ""},
                },
            ],
            "BLOCK_INDEX_OUT_OF_ORDER",
        ),
        (
            [
                text_response_events()[0],
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "image", "source": {}},
                },
            ],
            "UNKNOWN_BLOCK_TYPE",
        ),
        (
            [
                text_response_events()[0],
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": "{}"},
                },
            ],
            "BLOCK_TYPE_CONFLICT",
        ),
    ],
)
def test_out_of_order_or_conflicting_events_are_protocol_errors(
    events: list[dict[str, object]], code: str
) -> None:
    """Accepting malformed lifecycle events could commit a corrupted assistant turn."""
    assembler = MessageStreamAssembler()

    with pytest.raises(ModelProtocolError) as raised:
        feed_all(assembler, events)

    assert raised.value.code == code


def test_duplicate_tool_use_id_is_rejected() -> None:
    """Duplicate IDs would make later tool results ambiguous."""
    events = list(two_tool_use_event_stream())
    second_start = events[8]
    assert isinstance(second_start["content_block"], dict)
    second_start["content_block"]["id"] = "tool-1"
    assembler = MessageStreamAssembler()

    with pytest.raises(ModelProtocolError) as raised:
        feed_all(assembler, events)

    assert raised.value.code == "DUPLICATE_TOOL_USE_ID"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing_block_stop", "UNCLOSED_CONTENT_BLOCK"),
        ("missing_message_delta", "MISSING_MESSAGE_DELTA"),
        ("missing_message_stop", "MISSING_MESSAGE_STOP"),
    ],
)
def test_incomplete_streams_never_form_assistant_turns(mutation: str, code: str) -> None:
    """Finishing incomplete data would make partial model output executable."""
    events = list(two_tool_use_event_stream())
    if mutation == "missing_block_stop":
        events = [event for index, event in enumerate(events) if index != 10]
    elif mutation == "missing_message_delta":
        events = [event for event in events if event["type"] != "message_delta"]
    else:
        events = [event for event in events if event["type"] != "message_stop"]

    assembler = MessageStreamAssembler()
    try:
        feed_all(assembler, events)
    except ModelProtocolError as error:
        assert error.code == code
        return

    with pytest.raises(ModelProtocolError) as raised:
        assembler.finish()
    assert raised.value.code == code


@pytest.mark.parametrize(
    ("partial_json", "code"),
    [
        ('{"command":', "INVALID_TOOL_INPUT_JSON"),
        ("[1, 2]", "TOOL_INPUT_NOT_OBJECT"),
    ],
)
def test_complete_but_unusable_tool_input_stays_correctable(partial_json: str, code: str) -> None:
    """Spec 8.3 answers a valid call identity with a tool error the model can fix itself."""
    events = list(two_tool_use_event_stream())
    delta = events[9]
    assert isinstance(delta["delta"], dict)
    delta["delta"]["partial_json"] = partial_json
    assembler = MessageStreamAssembler()
    feed_all(assembler, events)

    turn = assembler.finish()

    assert [call.id for call in turn.tool_calls] == ["tool-1", "tool-2"]
    assert dict(turn.tool_calls[0].input) == {"path": "a.py"}
    assert dict(turn.tool_calls[1].input) == {}
    assert set(turn.invalid_tool_arguments) == {"tool-2"}
    assert turn.invalid_tool_arguments["tool-2"].code == code
    assert turn.invalid_tool_arguments["tool-2"].message


def test_max_tokens_with_tool_use_is_never_returned_as_executable() -> None:
    """Returning a truncated tool call would allow the Agent Loop to execute partial input."""
    events = list(two_tool_use_event_stream())
    message_delta = events[-3]
    assert isinstance(message_delta["delta"], dict)
    message_delta["delta"]["stop_reason"] = "max_tokens"
    assembler = MessageStreamAssembler()
    feed_all(assembler, events)

    with pytest.raises(ModelProtocolError) as raised:
        assembler.finish()

    assert raised.value.code == "INCOMPLETE_TOOL_CALL"


def test_max_tokens_with_incomplete_tool_json_still_reports_incomplete_call() -> None:
    """JSON parsing must not hide the stronger max_tokens tool truncation signal."""
    events = list(two_tool_use_event_stream())
    tool_delta = events[9]
    message_delta = events[-3]
    assert isinstance(tool_delta["delta"], dict)
    assert isinstance(message_delta["delta"], dict)
    tool_delta["delta"]["partial_json"] = '{"command":'
    message_delta["delta"]["stop_reason"] = "max_tokens"
    assembler = MessageStreamAssembler()
    feed_all(assembler, events)

    with pytest.raises(ModelProtocolError) as raised:
        assembler.finish()

    assert raised.value.code == "INCOMPLETE_TOOL_CALL"


def test_tool_identity_cannot_change_after_block_start() -> None:
    """Changing a locked name could bind accumulated JSON to a different local tool."""
    assembler = MessageStreamAssembler()
    events = list(two_tool_use_event_stream())
    start = events[0]
    block_start = dict(events[4])
    block_start["index"] = 0
    assembler.feed(normalized(start))
    assembler.feed(normalized(block_start))

    with pytest.raises(ModelProtocolError) as raised:
        assembler.feed(
            NormalizedMessageEvent(
                type="content_block_delta",
                index=0,
                block_type="tool_use",
                block_id="tool-1",
                block_name="write_file",
                delta_type="input_json_delta",
                partial_json="{}",
            )
        )

    assert raised.value.code == "TOOL_USE_NAME_CONFLICT"


@pytest.mark.parametrize("initial_input", [None, []])
def test_tool_use_start_requires_object_input(initial_input: object) -> None:
    """Treating a missing or non-object start input as {} would hide malformed wire data."""
    content_block: dict[str, object] = {
        "type": "tool_use",
        "id": "tool-1",
        "name": "read_file",
    }
    if initial_input is not None:
        content_block["input"] = initial_input
    events = [
        text_response_events()[0],
        {"type": "content_block_start", "index": 0, "content_block": content_block},
    ]
    assembler = MessageStreamAssembler()

    with pytest.raises(ModelProtocolError) as raised:
        feed_all(assembler, events)

    assert raised.value.code == "INVALID_TOOL_USE_INPUT"


def test_nonempty_initial_tool_input_conflicts_with_partial_json() -> None:
    """Silently preferring partial JSON would discard valid input from block start."""
    events = list(two_tool_use_event_stream())
    block_start = events[4]
    content_block = block_start["content_block"]
    assert isinstance(content_block, dict)
    content_block["input"] = {"path": "seed.py"}
    assembler = MessageStreamAssembler()

    with pytest.raises(ModelProtocolError) as raised:
        feed_all(assembler, events[:6])

    assert raised.value.code == "TOOL_INPUT_SOURCE_CONFLICT"


def test_tool_use_stop_without_tool_block_is_a_protocol_error() -> None:
    """A tool-use stop without a call cannot be continued safely."""
    assembler = MessageStreamAssembler()
    feed_all(assembler, text_response_events(stop_reason="tool_use"))

    with pytest.raises(ModelProtocolError) as raised:
        assembler.finish()

    assert raised.value.code == "TOOL_USE_WITHOUT_BLOCK"


def test_complete_tool_block_wins_over_inconsistent_end_turn_reason() -> None:
    """Dropping a complete call because of end_turn would lose explicit model intent."""
    events = list(two_tool_use_event_stream())
    message_delta = events[-3]
    assert isinstance(message_delta["delta"], dict)
    message_delta["delta"]["stop_reason"] = "end_turn"
    assembler = MessageStreamAssembler()
    feed_all(assembler, events)

    turn = assembler.finish()

    assert len(turn.tool_calls) == 2
    assert assembler.diagnostics == ("TOOL_USE_STOP_REASON_MISMATCH",)


def test_stream_error_is_a_typed_transport_error() -> None:
    """Returning stream errors as content would pollute the canonical transcript."""
    assembler = MessageStreamAssembler()

    with pytest.raises(ModelTransportError) as raised:
        assembler.feed(
            NormalizedMessageEvent(type="error", error_type="overloaded_error", error_detail="busy")
        )

    assert raised.value.retryable is True


def test_events_after_cancellation_are_rejected() -> None:
    """Late network frames must not revive a cancelled model request."""
    assembler = MessageStreamAssembler()
    assembler.cancel()

    with pytest.raises(ModelProtocolError) as raised:
        assembler.feed(normalized(text_response_events()[0]))

    assert raised.value.code == "STREAM_CANCELLED"


def test_events_after_message_stop_are_rejected() -> None:
    """Late frames after the terminal event must not mutate a completed turn."""
    assembler = MessageStreamAssembler()
    feed_all(assembler, text_response_events())

    with pytest.raises(ModelProtocolError) as raised:
        assembler.feed(NormalizedMessageEvent(type="ping"))

    assert raised.value.code == "EVENT_AFTER_MESSAGE_STOP"
