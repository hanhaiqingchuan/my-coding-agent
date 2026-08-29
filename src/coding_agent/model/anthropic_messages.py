"""Anthropic-compatible streaming Messages adapter."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic

from coding_agent.config import ModelSettings
from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.errors import CancellationRequested
from coding_agent.core.models import (
    AssistantTurn,
    JsonValue,
    TextPart,
    ThinkingPart,
    ToolResult,
    ToolUsePart,
)
from coding_agent.model.message_assembler import MessageStreamAssembler, NormalizedMessageEvent
from coding_agent.model.protocol import (
    DeltaSink,
    ModelAPIError,
    ModelProtocolError,
    ModelRequest,
    ModelTransportError,
    StreamNotification,
    TextDelta,
    ThinkingBlockClosedSink,
    ThinkingDelta,
    ThinkingDeltaSink,
)

_CONTEXT_ERROR_TYPES = {
    "context_length_exceeded",
    "context_too_large",
    "context_window_exceeded",
    "model_context_window_exceeded",
    "prompt_is_too_long",
    "request_too_large",
}
_RETRYABLE_ERROR_TYPES = {"api_error", "overloaded_error", "rate_limit_error"}


class AnthropicMessagesModel:
    def __init__(self, settings: ModelSettings, api_key: str) -> None:
        if not settings.stream:
            raise ValueError("Anthropic Messages P0 requires stream=true")
        self._settings = settings
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=settings.base_url,
            max_retries=0,
        )

    async def complete(
        self,
        request: ModelRequest,
        on_text_delta: DeltaSink,
        cancellation: CancellationToken,
        *,
        on_thinking_delta: ThinkingDeltaSink | None = None,
        on_thinking_block_closed: ThinkingBlockClosedSink | None = None,
    ) -> AssistantTurn:
        try:
            cancellation.raise_if_cancelled()
            payload = _request_payload(self._settings.model, request)
            stream = await _create_stream(self._client.messages.create(**payload), cancellation)
            if not hasattr(stream, "__aiter__"):
                raise ModelProtocolError(
                    "NON_STREAMING_RESPONSE", "Messages API did not return an async stream"
                )
            return await _consume_stream(
                stream,
                on_text_delta,
                cancellation,
                on_thinking_delta=on_thinking_delta,
                on_thinking_block_closed=on_thinking_block_closed,
            )
        except (CancellationRequested, ModelAPIError, ModelProtocolError, ModelTransportError):
            raise
        except APIStatusError as error:
            raise _map_status_error(error) from error
        except APIConnectionError as error:
            raise ModelTransportError(retryable=True, cause=error) from error
        except Exception as error:
            raise ModelTransportError(retryable=False, cause=error) from error


async def _create_stream(request: Any, cancellation: CancellationToken) -> object:
    request_task = asyncio.ensure_future(request)
    cancelled = asyncio.create_task(cancellation.wait())
    try:
        done, _ = await asyncio.wait({request_task, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        if cancelled in done:
            request_task.cancel()
            with suppress(asyncio.CancelledError):
                await request_task
            cancellation.raise_if_cancelled()
        cancelled.cancel()
        with suppress(asyncio.CancelledError):
            await cancelled
        return request_task.result()
    except BaseException:
        for task in (request_task, cancelled):
            if not task.done():
                task.cancel()
        raise


def _request_payload(model: str, request: ModelRequest) -> dict[str, object]:
    # No `thinking` field: the portable set stays neutral and provider-default reasoning
    # is tolerated (spec 8.1). Thinking blocks are aggregated for display, never echoed.
    payload: dict[str, object] = {
        "model": model,
        "max_tokens": request.max_tokens,
        "system": request.system,
        "messages": _compile_messages(request),
        "stream": True,
    }
    if request.tools:
        payload["tools"] = [_compile_tool_schema(tool) for tool in request.tools]
        payload["tool_choice"] = {"type": "auto"}
    return payload


def _compile_messages(request: ModelRequest) -> list[dict[str, object]]:
    wire: list[tuple[dict[str, object], bool]] = []
    pending_tool_ids: tuple[str, ...] | None = None
    pending_result_blocks: dict[str, dict[str, object]] = {}

    for message in request.messages:
        if message.role == "assistant":
            if pending_tool_ids is not None:
                raise ModelProtocolError(
                    "MISSING_TOOL_RESULTS", "assistant tool use must be followed by user results"
                )
            content: list[dict[str, object]] = []
            tool_ids: list[str] = []
            for part in message.parts:
                if isinstance(part, TextPart):
                    content.append({"type": "text", "text": part.text})
                elif isinstance(part, ThinkingPart):
                    # Display-only reasoning: never echoed back to the provider.
                    continue
                elif isinstance(part, ToolUsePart):
                    call = part.call
                    content.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": _thaw_json(call.input),
                        }
                    )
                    tool_ids.append(call.id)
                else:  # pragma: no cover - ModelMessage validates this invariant
                    raise ModelProtocolError(
                        "INVALID_ASSISTANT_PART", "assistant message contains a user-only part"
                    )
            _append_wire_message(wire, "assistant", content, atomic=bool(tool_ids))
            if tool_ids:
                pending_tool_ids = tuple(tool_ids)
                pending_result_blocks = {}
            continue

        has_results = any(isinstance(part, ToolResult) for part in message.parts)
        has_text = any(isinstance(part, TextPart) for part in message.parts)
        if has_results and has_text:
            raise ModelProtocolError(
                "MIXED_TOOL_RESULTS", "tool results must occupy their own user message"
            )
        if has_results:
            if pending_tool_ids is None:
                raise ModelProtocolError(
                    "ORPHAN_TOOL_RESULT", "tool result has no preceding assistant tool use"
                )
            results = tuple(part for part in message.parts if isinstance(part, ToolResult))
            for result in results:
                if result.tool_call_id not in pending_tool_ids:
                    raise ModelProtocolError(
                        "TOOL_RESULT_MISMATCH",
                        f"unexpected tool result id {result.tool_call_id!r}",
                    )
                if result.tool_call_id in pending_result_blocks:
                    raise ModelProtocolError(
                        "DUPLICATE_TOOL_RESULT_ID",
                        f"duplicate tool result id {result.tool_call_id!r}",
                    )
                block: dict[str, object] = {
                    "type": "tool_result",
                    "tool_use_id": result.tool_call_id,
                    "content": result.content,
                }
                if not result.ok:
                    block["is_error"] = True
                pending_result_blocks[result.tool_call_id] = block
            if len(pending_result_blocks) == len(pending_tool_ids):
                ordered_blocks = [pending_result_blocks[tool_id] for tool_id in pending_tool_ids]
                _append_wire_message(wire, "user", ordered_blocks, atomic=True)
                pending_tool_ids = None
                pending_result_blocks = {}
            continue

        if pending_tool_ids is not None:
            raise ModelProtocolError(
                "MISSING_TOOL_RESULTS", "assistant tool use must be followed by user results"
            )
        blocks = [
            {"type": "text", "text": part.text}
            for part in message.parts
            if isinstance(part, TextPart)
        ]
        _append_wire_message(wire, "user", blocks, atomic=False)

    if pending_tool_ids is not None:
        raise ModelProtocolError(
            "MISSING_TOOL_RESULTS", "assistant tool use has no following result message"
        )
    return [message for message, _ in wire]


def _append_wire_message(
    wire: list[tuple[dict[str, object], bool]],
    role: str,
    content: list[dict[str, object]],
    *,
    atomic: bool,
) -> None:
    if wire and wire[-1][0]["role"] == role and not wire[-1][1]:
        previous = wire[-1][0]["content"]
        if not isinstance(previous, list):  # pragma: no cover - constructed above
            raise AssertionError("wire content must be a list")
        previous.extend(content)
        if atomic:
            wire[-1] = (wire[-1][0], True)
        return
    wire.append(({"role": role, "content": content}, atomic))


def _compile_tool_schema(schema: Mapping[str, JsonValue]) -> dict[str, object]:
    name = schema.get("name")
    description = schema.get("description")
    input_schema = schema.get("input_schema")
    if not isinstance(name, str) or not name:
        raise ModelProtocolError("INVALID_TOOL_SCHEMA", "tool schema requires a name")
    if not isinstance(description, str):
        raise ModelProtocolError("INVALID_TOOL_SCHEMA", f"tool {name!r} requires a description")
    if not isinstance(input_schema, Mapping):
        raise ModelProtocolError("INVALID_TOOL_SCHEMA", f"tool {name!r} requires input_schema")
    return {
        "name": name,
        "description": description,
        "input_schema": _thaw_json(input_schema),
    }


async def _consume_stream(
    stream: object,
    on_text_delta: DeltaSink,
    cancellation: CancellationToken,
    *,
    on_thinking_delta: ThinkingDeltaSink | None = None,
    on_thinking_block_closed: ThinkingBlockClosedSink | None = None,
) -> AssistantTurn:
    assembler = MessageStreamAssembler()
    iterator = stream.__aiter__()  # type: ignore[attr-defined]
    try:
        while True:
            cancellation.raise_if_cancelled()
            next_event = asyncio.create_task(anext(iterator))
            cancelled = asyncio.create_task(cancellation.wait())
            done, _ = await asyncio.wait(
                {next_event, cancelled}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancelled in done:
                assembler.cancel()
                next_event.cancel()
                with suppress(asyncio.CancelledError, StopAsyncIteration):
                    await next_event
                cancellation.raise_if_cancelled()
            cancelled.cancel()
            with suppress(asyncio.CancelledError):
                await cancelled
            try:
                raw_event = next_event.result()
            except StopAsyncIteration:
                break
            event = NormalizedMessageEvent.from_sdk_event(raw_event)
            for notification in assembler.feed(event):
                result = _dispatch_notification(
                    notification,
                    on_text_delta,
                    on_thinking_delta,
                    on_thinking_block_closed,
                )
                if inspect.isawaitable(result):
                    await result
        return assembler.finish()
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result


def _dispatch_notification(
    notification: StreamNotification,
    on_text_delta: DeltaSink,
    on_thinking_delta: ThinkingDeltaSink | None,
    on_thinking_block_closed: ThinkingBlockClosedSink | None,
) -> object:
    if isinstance(notification, TextDelta):
        return on_text_delta(notification)
    if isinstance(notification, ThinkingDelta):
        return on_thinking_delta(notification) if on_thinking_delta is not None else None
    return on_thinking_block_closed(notification) if on_thinking_block_closed is not None else None


def _map_status_error(error: APIStatusError) -> ModelAPIError:
    status_code = error.status_code
    structured_type = _structured_error_type(error.body)
    if structured_type in _CONTEXT_ERROR_TYPES:
        error_type = "context_too_large"
    else:
        error_type = structured_type

    headers = error.response.headers
    retry_override = headers.get("x-should-retry", "").strip().lower()
    has_retry_override = retry_override in {"true", "false"}
    if retry_override == "true":
        retryable = True
    elif retry_override == "false":
        retryable = False
    else:
        retryable = (
            structured_type in _RETRYABLE_ERROR_TYPES
            or status_code in {408, 409, 429}
            or status_code >= 500
        )
    if error_type == "context_too_large" and not has_retry_override:
        retryable = False
    return ModelAPIError(
        status_code=status_code,
        error_type=error_type,
        retry_after=headers.get("retry-after"),
        retryable=retryable,
        retry_after_ms=headers.get("retry-after-ms"),
    )


def _structured_error_type(body: object) -> str | None:
    if not isinstance(body, Mapping):
        return None
    nested = body.get("error")
    error = nested if isinstance(nested, Mapping) else body
    code = error.get("code")
    error_type = error.get("type")
    if isinstance(code, str) and code in _CONTEXT_ERROR_TYPES:
        return code
    if isinstance(error_type, str):
        return error_type
    if isinstance(code, str):
        return code
    outer_type = body.get("type")
    return outer_type if isinstance(outer_type, str) and outer_type != "error" else None


def _thaw_json(value: JsonValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


__all__ = ["AnthropicMessagesModel"]
