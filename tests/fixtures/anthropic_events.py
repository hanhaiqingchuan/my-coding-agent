from __future__ import annotations

from collections.abc import Iterable


def text_response_events(
    text: str = "done",
    *,
    stop_reason: str = "end_turn",
) -> list[dict[str, object]]:
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg-1",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "test-model",
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 3,
                    "cache_read_input_tokens": 4,
                },
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {
                "input_tokens": 13,
                "output_tokens": 5,
                "cache_creation_input_tokens": 6,
                "cache_read_input_tokens": 7,
            },
        },
        {"type": "message_stop"},
    ]


def two_tool_use_event_stream() -> Iterable[dict[str, object]]:
    yield {
        "type": "message_start",
        "message": {
            "id": "msg-tools",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "test-model",
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 20,
                "output_tokens": 1,
                "cache_creation_input_tokens": 2,
                "cache_read_input_tokens": 3,
            },
        },
    }
    yield {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    }
    yield {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "Checking files."},
    }
    yield {"type": "content_block_stop", "index": 0}
    yield {
        "type": "content_block_start",
        "index": 1,
        "content_block": {
            "type": "tool_use",
            "id": "tool-1",
            "name": "read_file",
            "input": {},
        },
    }
    yield {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": '{"path":'},
    }
    yield {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": '"a.py"}'},
    }
    yield {"type": "content_block_stop", "index": 1}
    yield {
        "type": "content_block_start",
        "index": 2,
        "content_block": {
            "type": "tool_use",
            "id": "tool-2",
            "name": "run_command",
            "input": {},
        },
    }
    yield {
        "type": "content_block_delta",
        "index": 2,
        "delta": {"type": "input_json_delta", "partial_json": '{"command":"pytest"}'},
    }
    yield {"type": "content_block_stop", "index": 2}
    yield {
        "type": "message_delta",
        "delta": {"stop_reason": "tool_use", "stop_sequence": None},
        "usage": {
            "input_tokens": 21,
            "output_tokens": 9,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 8,
        },
    }
    yield {"type": "ping"}
    yield {"type": "message_stop"}
