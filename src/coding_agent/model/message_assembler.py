"""Strict semantic assembly of normalized Anthropic Messages stream events."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from coding_agent.core.models import (
    AssistantPart,
    AssistantTurn,
    ModelStopReason,
    TextPart,
    ToolCall,
    ToolUsePart,
    Usage,
)
from coding_agent.model.protocol import ModelProtocolError, ModelTransportError, TextDelta


@dataclass(frozen=True, slots=True)
class NormalizedMessageEvent:
    type: str
    message_id: str | None = None
    index: int | None = None
    block_type: str | None = None
    block_id: str | None = None
    block_name: str | None = None
    initial_text: str | None = None
    initial_input: Mapping[str, object] | None = None
    delta_type: str | None = None
    text: str | None = None
    partial_json: str | None = None
    stop_reason: str | None = None
    usage: Usage | None = None
    error_type: str | None = None
    error_detail: str | None = None

    @classmethod
    def from_mapping(cls, event: Mapping[str, object]) -> NormalizedMessageEvent:
        event_type = event.get("type")
        if not isinstance(event_type, str):
            raise ModelProtocolError("MISSING_EVENT_TYPE", "stream event type is required")
        if event_type == "message_start":
            message = _mapping(event.get("message"))
            return cls(
                type=event_type,
                message_id=_optional_str(message.get("id")),
                usage=_usage(message.get("usage")),
            )
        if event_type == "content_block_start":
            block = _mapping(event.get("content_block"))
            return cls(
                type=event_type,
                index=_optional_int(event.get("index")),
                block_type=_optional_str(block.get("type")),
                block_id=_optional_str(block.get("id")),
                block_name=_optional_str(block.get("name")),
                initial_text=_optional_str(block.get("text")),
                initial_input=_optional_mapping(block.get("input")),
            )
        if event_type == "content_block_delta":
            delta = _mapping(event.get("delta"))
            return cls(
                type=event_type,
                index=_optional_int(event.get("index")),
                delta_type=_optional_str(delta.get("type")),
                text=_optional_str(delta.get("text")),
                partial_json=_optional_str(delta.get("partial_json")),
            )
        if event_type == "content_block_stop":
            return cls(type=event_type, index=_optional_int(event.get("index")))
        if event_type == "message_delta":
            delta = _mapping(event.get("delta"))
            return cls(
                type=event_type,
                stop_reason=_optional_str(delta.get("stop_reason")),
                usage=_usage(event.get("usage")),
            )
        if event_type == "error":
            error = _mapping(event.get("error"))
            return cls(
                type=event_type,
                error_type=_optional_str(error.get("type") or event.get("error_type")),
                error_detail=_optional_str(error.get("message") or event.get("error_detail")),
            )
        return cls(type=event_type)

    @classmethod
    def from_sdk_event(cls, event: object) -> NormalizedMessageEvent:
        if isinstance(event, Mapping):
            return cls.from_mapping(event)
        model_dump = getattr(event, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(mode="python")
            if isinstance(dumped, Mapping):
                return cls.from_mapping(dumped)
        raise ModelProtocolError(
            "INVALID_SDK_EVENT", f"cannot normalize SDK event {type(event).__name__}"
        )


@dataclass(slots=True)
class _ContentBlock:
    type: str
    id: str | None = None
    name: str | None = None
    initial_input: Mapping[str, object] | None = None
    fragments: list[str] = field(default_factory=list)
    closed: bool = False


class MessageStreamAssembler:
    def __init__(self) -> None:
        self._cancelled = False
        self._message_id: str | None = None
        self._blocks: list[_ContentBlock] = []
        self._usage = Usage()
        self._stop_reason: ModelStopReason | None = None
        self._message_delta_seen = False
        self._message_stop_seen = False
        self._diagnostics: list[str] = []
        self._finished_turn: AssistantTurn | None = None

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._diagnostics)

    def cancel(self) -> None:
        self._cancelled = True

    def feed(self, event: NormalizedMessageEvent) -> Sequence[TextDelta]:
        if self._cancelled:
            raise ModelProtocolError("STREAM_CANCELLED", "event arrived after cancellation")
        if self._message_stop_seen:
            raise ModelProtocolError("EVENT_AFTER_MESSAGE_STOP", "event arrived after message_stop")
        if event.type == "ping":
            return ()
        if event.type == "error":
            detail = event.error_detail or event.error_type or "unknown stream error"
            raise ModelTransportError(True, RuntimeError(detail))
        if event.type == "message_start":
            self._feed_message_start(event)
            return ()
        if self._message_id is None:
            raise ModelProtocolError(
                "MESSAGE_NOT_STARTED", f"{event.type} arrived before message_start"
            )
        if event.type == "content_block_start":
            self._feed_block_start(event)
            return ()
        if event.type == "content_block_delta":
            return self._feed_block_delta(event)
        if event.type == "content_block_stop":
            self._feed_block_stop(event)
            return ()
        if event.type == "message_delta":
            self._feed_message_delta(event)
            return ()
        if event.type == "message_stop":
            self._feed_message_stop()
            return ()
        raise ModelProtocolError("UNKNOWN_EVENT_TYPE", f"unsupported event type {event.type!r}")

    def finish(self) -> AssistantTurn:
        if self._finished_turn is not None:
            return self._finished_turn
        if self._message_id is None:
            raise ModelProtocolError("MISSING_MESSAGE_START", "stream has no message_start")
        if any(not block.closed for block in self._blocks):
            raise ModelProtocolError("UNCLOSED_CONTENT_BLOCK", "a content block is still open")
        if not self._message_delta_seen:
            raise ModelProtocolError("MISSING_MESSAGE_DELTA", "stream has no message_delta")
        if not self._message_stop_seen:
            raise ModelProtocolError("MISSING_MESSAGE_STOP", "stream has no message_stop")
        if self._stop_reason is None:  # pragma: no cover - guarded by message_delta
            raise ModelProtocolError("MISSING_STOP_REASON", "message_delta has no stop reason")

        has_tool_use = any(block.type == "tool_use" for block in self._blocks)
        if self._stop_reason is ModelStopReason.MAX_TOKENS and has_tool_use:
            raise ModelProtocolError(
                "INCOMPLETE_TOOL_CALL",
                "max_tokens ended a response containing tool use",
                usage=self._usage,
            )
        parts = tuple(self._build_part(block) for block in self._blocks)
        if self._stop_reason is ModelStopReason.TOOL_USE and not has_tool_use:
            raise ModelProtocolError(
                "TOOL_USE_WITHOUT_BLOCK", "tool_use stop reason has no tool_use block"
            )
        if has_tool_use and self._stop_reason in {
            ModelStopReason.END_TURN,
            ModelStopReason.STOP_SEQUENCE,
        }:
            self._diagnostics.append("TOOL_USE_STOP_REASON_MISMATCH")
        self._finished_turn = AssistantTurn(
            id=self._message_id,
            parts=parts,
            stop_reason=self._stop_reason,
            usage=self._usage,
        )
        return self._finished_turn

    def _feed_message_start(self, event: NormalizedMessageEvent) -> None:
        if self._message_id is not None:
            raise ModelProtocolError("DUPLICATE_MESSAGE_START", "message_start appeared twice")
        if not event.message_id:
            raise ModelProtocolError("MISSING_MESSAGE_ID", "message_start requires an id")
        if event.usage is None:
            raise ModelProtocolError(
                "MISSING_MESSAGE_USAGE", "message_start requires provider usage"
            )
        self._message_id = event.message_id
        self._usage = _merge_usage(self._usage, event.usage)

    def _feed_block_start(self, event: NormalizedMessageEvent) -> None:
        if self._message_delta_seen:
            raise ModelProtocolError(
                "BLOCK_AFTER_MESSAGE_DELTA", "content block started after message_delta"
            )
        if event.index is None or event.index < 0:
            raise ModelProtocolError("MISSING_BLOCK_INDEX", "content block requires an index")
        if event.index != len(self._blocks):
            code = (
                "DUPLICATE_BLOCK_INDEX"
                if event.index < len(self._blocks)
                else "BLOCK_INDEX_OUT_OF_ORDER"
            )
            raise ModelProtocolError(code, f"unexpected content block index {event.index}")
        if self._blocks and not self._blocks[-1].closed:
            raise ModelProtocolError(
                "UNCLOSED_CONTENT_BLOCK", "a new content block started before the prior stop"
            )
        if event.block_type == "text":
            if event.initial_text is None:
                raise ModelProtocolError("MISSING_BLOCK_TEXT", "text block start requires text")
            block = _ContentBlock(type="text", fragments=[event.initial_text])
        elif event.block_type == "tool_use":
            if not event.block_id:
                raise ModelProtocolError("MISSING_TOOL_USE_ID", "tool_use block requires an id")
            if not event.block_name:
                raise ModelProtocolError("MISSING_TOOL_USE_NAME", "tool_use block requires a name")
            if not isinstance(event.initial_input, Mapping):
                raise ModelProtocolError(
                    "INVALID_TOOL_USE_INPUT", "tool_use block start requires object input"
                )
            if any(existing.id == event.block_id for existing in self._blocks):
                raise ModelProtocolError(
                    "DUPLICATE_TOOL_USE_ID", f"duplicate tool_use id {event.block_id!r}"
                )
            block = _ContentBlock(
                type="tool_use",
                id=event.block_id,
                name=event.block_name,
                initial_input=event.initial_input,
            )
        else:
            raise ModelProtocolError(
                "UNKNOWN_BLOCK_TYPE", f"unsupported content block type {event.block_type!r}"
            )
        self._blocks.append(block)

    def _feed_block_delta(self, event: NormalizedMessageEvent) -> Sequence[TextDelta]:
        block = self._open_block(event.index)
        self._validate_block_identity(block, event)
        if event.delta_type == "text_delta":
            if block.type != "text":
                raise ModelProtocolError(
                    "BLOCK_TYPE_CONFLICT", "text delta targeted a non-text block"
                )
            if event.text is None:
                raise ModelProtocolError("MISSING_TEXT_DELTA", "text_delta requires text")
            block.fragments.append(event.text)
            return (TextDelta(index=_required_index(event.index), text=event.text),)
        if event.delta_type == "input_json_delta":
            if block.type != "tool_use":
                raise ModelProtocolError(
                    "BLOCK_TYPE_CONFLICT", "input JSON delta targeted a non-tool block"
                )
            if event.partial_json is None:
                raise ModelProtocolError(
                    "MISSING_PARTIAL_JSON", "input_json_delta requires partial_json"
                )
            if block.initial_input:
                raise ModelProtocolError(
                    "TOOL_INPUT_SOURCE_CONFLICT",
                    "tool input cannot come from both block start and partial JSON",
                )
            block.fragments.append(event.partial_json)
            return ()
        raise ModelProtocolError(
            "UNKNOWN_DELTA_TYPE", f"unsupported content delta type {event.delta_type!r}"
        )

    def _feed_block_stop(self, event: NormalizedMessageEvent) -> None:
        if event.index is None or event.index < 0:
            raise ModelProtocolError("MISSING_BLOCK_INDEX", "content block stop requires an index")
        if event.index >= len(self._blocks):
            raise ModelProtocolError(
                "BLOCK_NOT_STARTED", f"content block {event.index} was not started"
            )
        block = self._blocks[event.index]
        if block.closed:
            raise ModelProtocolError(
                "BLOCK_ALREADY_STOPPED", f"content block {event.index} was already stopped"
            )
        if event.index != len(self._blocks) - 1:
            raise ModelProtocolError(
                "BLOCK_INDEX_OUT_OF_ORDER", f"content block {event.index} is not active"
            )
        block.closed = True

    def _feed_message_delta(self, event: NormalizedMessageEvent) -> None:
        if self._message_delta_seen:
            raise ModelProtocolError("DUPLICATE_MESSAGE_DELTA", "message_delta appeared twice")
        if any(not block.closed for block in self._blocks):
            raise ModelProtocolError(
                "UNCLOSED_CONTENT_BLOCK", "message_delta arrived before content_block_stop"
            )
        if event.stop_reason is None:
            raise ModelProtocolError("MISSING_STOP_REASON", "message_delta requires stop_reason")
        try:
            stop_reason = ModelStopReason(event.stop_reason)
        except ValueError as error:
            raise ModelProtocolError(
                "UNKNOWN_STOP_REASON", f"unsupported stop reason {event.stop_reason!r}"
            ) from error
        self._stop_reason = stop_reason
        if event.usage is None:
            raise ModelProtocolError(
                "MISSING_MESSAGE_USAGE", "message_delta requires provider usage"
            )
        self._usage = _merge_usage(self._usage, event.usage)
        self._message_delta_seen = True

    def _feed_message_stop(self) -> None:
        if not self._message_delta_seen:
            raise ModelProtocolError(
                "MISSING_MESSAGE_DELTA", "message_stop arrived before message_delta"
            )
        if any(not block.closed for block in self._blocks):
            raise ModelProtocolError(
                "UNCLOSED_CONTENT_BLOCK", "message_stop arrived before content_block_stop"
            )
        self._message_stop_seen = True

    def _open_block(self, index: int | None) -> _ContentBlock:
        if index is None or index < 0:
            raise ModelProtocolError("MISSING_BLOCK_INDEX", "content delta requires an index")
        if index >= len(self._blocks):
            raise ModelProtocolError("BLOCK_NOT_STARTED", f"content block {index} was not started")
        block = self._blocks[index]
        if block.closed:
            raise ModelProtocolError("BLOCK_ALREADY_STOPPED", f"content block {index} is closed")
        if index != len(self._blocks) - 1:
            raise ModelProtocolError(
                "BLOCK_INDEX_OUT_OF_ORDER", f"content block {index} is not active"
            )
        return block

    def _validate_block_identity(self, block: _ContentBlock, event: NormalizedMessageEvent) -> None:
        if event.block_type is not None and event.block_type != block.type:
            raise ModelProtocolError(
                "BLOCK_TYPE_CONFLICT", "content block type changed after start"
            )
        if event.block_id is not None and event.block_id != block.id:
            raise ModelProtocolError(
                "TOOL_USE_ID_CONFLICT", "tool_use id changed after block start"
            )
        if event.block_name is not None and event.block_name != block.name:
            raise ModelProtocolError(
                "TOOL_USE_NAME_CONFLICT", "tool_use name changed after block start"
            )

    def _build_part(self, block: _ContentBlock) -> AssistantPart:
        if block.type == "text":
            return TextPart("".join(block.fragments))
        raw_input = "".join(block.fragments)
        if raw_input:
            try:
                parsed = json.loads(raw_input)
            except json.JSONDecodeError as error:
                raise ModelProtocolError(
                    "INVALID_TOOL_INPUT_JSON", f"tool input is incomplete or invalid: {error.msg}"
                ) from error
        else:
            parsed = block.initial_input
        if not isinstance(parsed, Mapping):
            raise ModelProtocolError("TOOL_INPUT_NOT_OBJECT", "tool input must be a JSON object")
        return ToolUsePart(ToolCall(id=block.id or "", name=block.name or "", input=parsed))


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _optional_mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage(value: object) -> Usage | None:
    if not isinstance(value, Mapping):
        return None
    return Usage(
        input_tokens=_usage_value(value, "input_tokens"),
        output_tokens=_usage_value(value, "output_tokens"),
        cache_creation_input_tokens=_usage_value(value, "cache_creation_input_tokens"),
        cache_read_input_tokens=_usage_value(value, "cache_read_input_tokens"),
    )


def _usage_value(usage: Mapping[str, object], field: str) -> int | None:
    value = usage.get(field)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ModelProtocolError("INVALID_USAGE", f"{field} must be a non-negative integer or null")
    return value


def _merge_usage(current: Usage, update: Usage | None) -> Usage:
    if update is None:
        return current
    return Usage(
        input_tokens=(
            update.input_tokens if update.input_tokens is not None else current.input_tokens
        ),
        output_tokens=(
            update.output_tokens if update.output_tokens is not None else current.output_tokens
        ),
        cache_creation_input_tokens=(
            update.cache_creation_input_tokens
            if update.cache_creation_input_tokens is not None
            else current.cache_creation_input_tokens
        ),
        cache_read_input_tokens=(
            update.cache_read_input_tokens
            if update.cache_read_input_tokens is not None
            else current.cache_read_input_tokens
        ),
    )


def _required_index(index: int | None) -> int:
    assert index is not None
    return index


__all__ = ["MessageStreamAssembler", "NormalizedMessageEvent"]
