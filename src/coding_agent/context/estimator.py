"""Deterministic heuristic for sizing Anthropic-compatible model inputs.

The estimate is deliberately not a tokenizer result or an upper bound. It
uses ``ceil(UTF-8 wire bytes / 3)`` plus explicit protocol overheads so callers
can make reproducible preflight decisions and compare them with provider usage.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from coding_agent.core.models import TextPart, ThinkingPart, ToolResult, ToolUsePart
from coding_agent.model.protocol import ModelMessage

ESTIMATOR_ID = "utf8-bytes-over-3-v1"

REQUEST_FIXED_OVERHEAD_TOKENS = 4
SYSTEM_FIXED_OVERHEAD_TOKENS = 4
MESSAGE_FIXED_OVERHEAD_TOKENS = 4
CONTENT_BLOCK_FIXED_OVERHEAD_TOKENS = 3
TOOL_SCHEMA_FIXED_OVERHEAD_TOKENS = 6


def estimate_input_tokens(
    system: str,
    messages: Sequence[ModelMessage],
    tool_schemas: Sequence[Mapping[str, object]],
) -> int:
    """Estimate request input tokens without mutating or compiling the input."""
    if not isinstance(system, str):
        raise TypeError("system must be a string")

    total = REQUEST_FIXED_OVERHEAD_TOKENS + SYSTEM_FIXED_OVERHEAD_TOKENS
    total += _utf8_byte_tokens(system)

    for message in messages:
        total += MESSAGE_FIXED_OVERHEAD_TOKENS
        total += _utf8_byte_tokens(message.role)
        for part in message.parts:
            wire_part = _wire_part(part)
            if wire_part is None:
                continue
            total += CONTENT_BLOCK_FIXED_OVERHEAD_TOKENS
            total += _utf8_byte_tokens(_wire_json(wire_part))

    for schema in tool_schemas:
        if not isinstance(schema, Mapping):
            raise TypeError("tool schema must be a mapping")
        total += TOOL_SCHEMA_FIXED_OVERHEAD_TOKENS
        total += _utf8_byte_tokens(_wire_json(schema))

    return total


def _utf8_byte_tokens(value: str) -> int:
    byte_count = len(value.encode("utf-8"))
    return (byte_count + 2) // 3


def _wire_json(value: object) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _wire_part(
    part: TextPart | ThinkingPart | ToolUsePart | ToolResult,
) -> Mapping[str, object] | None:
    """Map a part to its wire block, or ``None`` when the part never reaches the wire."""
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ThinkingPart):
        # Display-only reasoning never compiles to a wire block, so it never counts.
        return None
    if isinstance(part, ToolUsePart):
        return {
            "type": "tool_use",
            "id": part.call.id,
            "name": part.call.name,
            "input": part.call.input,
        }
    if isinstance(part, ToolResult):
        block: dict[str, object] = {
            "type": "tool_result",
            "tool_use_id": part.tool_call_id,
            "content": part.content,
        }
        if not part.ok:
            block["is_error"] = True
        return block
    raise TypeError(f"unsupported model message part: {type(part).__name__}")


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if value is None or type(value) in {str, int, float, bool}:
        return value
    raise TypeError(f"expected a JSON value, got {type(value).__name__}")


__all__ = ["ESTIMATOR_ID", "estimate_input_tokens"]
