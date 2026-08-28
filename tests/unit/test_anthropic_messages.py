from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import anthropic
import httpx2
import pytest

from coding_agent.config import ModelSettings
from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.errors import CancellationRequested
from coding_agent.core.models import (
    AssistantTurn,
    ModelStopReason,
    TextPart,
    ToolCall,
    ToolError,
    ToolResult,
    ToolUsePart,
    Usage,
)
from coding_agent.model import anthropic_messages
from coding_agent.model.anthropic_messages import AnthropicMessagesModel
from coding_agent.model.protocol import (
    ModelAPIError,
    ModelMessage,
    ModelProtocolError,
    ModelRequest,
    ModelTransportError,
    TextDelta,
)
from tests.fixtures.anthropic_events import text_response_events


class FakeStream:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self._events = events
        self.closed = False

    def __aiter__(self) -> AsyncIterator[dict[str, object]]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[dict[str, object]]:
        for event in self._events:
            yield event

    async def close(self) -> None:
        self.closed = True


class BlockingStream:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = False

    def __aiter__(self) -> BlockingStream:
        return self

    async def __anext__(self) -> dict[str, object]:
        self.started.set()
        await asyncio.Future()
        raise StopAsyncIteration

    async def close(self) -> None:
        self.closed = True


class FailingStream:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.closed = False

    def __aiter__(self) -> FailingStream:
        return self

    async def __anext__(self) -> dict[str, object]:
        raise self._error

    async def close(self) -> None:
        self.closed = True


class FakeMessages:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class BlockingMessages:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def create(self, **kwargs: object) -> object:
        _ = kwargs
        self.started.set()
        await asyncio.Future()
        raise AssertionError("unreachable")


class FakeClient:
    def __init__(self, outcome: object) -> None:
        self.messages = FakeMessages(outcome)


def install_fake_client(
    monkeypatch: pytest.MonkeyPatch, outcome: object
) -> tuple[FakeClient, list[dict[str, object]]]:
    client = FakeClient(outcome)
    constructor_calls: list[dict[str, object]] = []

    def constructor(**kwargs: object) -> FakeClient:
        constructor_calls.append(kwargs)
        return client

    monkeypatch.setattr(anthropic_messages, "AsyncAnthropic", constructor)
    return client, constructor_calls


def request_with_tool_history(*, tools: bool = True) -> ModelRequest:
    read = ToolCall(id="tool-1", name="read_file", input={"path": "a.py"})
    run = ToolCall(id="tool-2", name="run_command", input={"command": "pytest"})
    return ModelRequest(
        system="system\ndeveloper\nenvironment",
        messages=(
            ModelMessage(role="user", parts=(TextPart("first"),)),
            ModelMessage(role="user", parts=(TextPart("second"),)),
            ModelMessage(
                role="assistant",
                parts=(
                    TextPart("inspect"),
                    ToolUsePart(read),
                    TextPart("then test"),
                    ToolUsePart(run),
                ),
            ),
            ModelMessage(
                role="user",
                parts=(ToolResult(tool_call_id="tool-1", content="file", ok=True),),
            ),
            ModelMessage(
                role="user",
                parts=(
                    ToolResult(
                        tool_call_id="tool-2",
                        content="failed",
                        ok=False,
                        error=ToolError(code="EXIT_NONZERO", message="exit 1"),
                    ),
                ),
            ),
            ModelMessage(role="user", parts=(TextPart("continue"),)),
        ),
        tools=(
            {
                "name": "read_file",
                "description": "Read a file.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        )
        if tools
        else (),
        max_tokens=321,
    )


@pytest.mark.asyncio
async def test_request_uses_only_portable_messages_fields_and_preserves_atomic_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding provider extras or merging across a tool boundary would break portability."""
    client, constructor_calls = install_fake_client(monkeypatch, FakeStream(text_response_events()))
    settings = ModelSettings(base_url="https://provider.example/api", model="model-a")
    model = AnthropicMessagesModel(settings, api_key="secret")
    seen_deltas: list[TextDelta] = []

    turn = await model.complete(
        request_with_tool_history(), seen_deltas.append, CancellationToken()
    )

    assert constructor_calls == [
        {
            "api_key": "secret",
            "base_url": "https://provider.example/api",
            "max_retries": 0,
        }
    ]
    assert len(client.messages.calls) == 1
    payload = client.messages.calls[0]
    assert set(payload) == {
        "model",
        "max_tokens",
        "system",
        "messages",
        "tools",
        "tool_choice",
        "stream",
    }
    assert payload["model"] == "model-a"
    assert payload["max_tokens"] == 321
    assert payload["system"] == "system\ndeveloper\nenvironment"
    assert payload["stream"] is True
    assert payload["tool_choice"] == {"type": "auto"}
    assert payload["tools"] == [
        {
            "name": "read_file",
            "description": "Read a file.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        }
    ]
    assert payload["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "inspect"},
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "read_file",
                    "input": {"path": "a.py"},
                },
                {"type": "text", "text": "then test"},
                {
                    "type": "tool_use",
                    "id": "tool-2",
                    "name": "run_command",
                    "input": {"command": "pytest"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tool-1", "content": "file"},
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-2",
                    "content": "failed",
                    "is_error": True,
                },
            ],
        },
        {"role": "user", "content": [{"type": "text", "text": "continue"}]},
    ]
    assert seen_deltas == [TextDelta(index=0, text="done")]
    assert turn.parts == (TextPart("done"),)


@pytest.mark.asyncio
async def test_toolless_request_omits_tools_and_tool_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sending empty tool controls would violate final-round and compaction semantics."""
    client, _ = install_fake_client(monkeypatch, FakeStream(text_response_events()))
    model = AnthropicMessagesModel(ModelSettings(model="model-a"), api_key="secret")

    await model.complete(
        request_with_tool_history(tools=False), lambda _: None, CancellationToken()
    )

    assert "tools" not in client.messages.calls[0]
    assert "tool_choice" not in client.messages.calls[0]


@pytest.mark.asyncio
async def test_mismatched_tool_results_fail_before_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sending a result for the wrong call ID would corrupt the Messages conversation."""
    client, _ = install_fake_client(monkeypatch, FakeStream(text_response_events()))
    call = ToolCall(id="tool-1", name="read_file", input={"path": "a.py"})
    request = ModelRequest(
        system="system",
        messages=(
            ModelMessage(role="assistant", parts=(ToolUsePart(call),)),
            ModelMessage(
                role="user",
                parts=(ToolResult(tool_call_id="wrong-id", content="x", ok=True),),
            ),
        ),
        tools=(),
        max_tokens=100,
    )
    model = AnthropicMessagesModel(ModelSettings(model="model-a"), api_key="secret")

    with pytest.raises(ModelProtocolError) as raised:
        await model.complete(request, lambda _: None, CancellationToken())

    assert raised.value.code == "TOOL_RESULT_MISMATCH"
    assert client.messages.calls == []


@pytest.mark.asyncio
async def test_tool_results_are_matched_by_id_then_emitted_in_call_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pairing by position would reject a complete batch that arrived out of order."""
    original = request_with_tool_history()
    messages = list(original.messages)
    messages[3], messages[4] = messages[4], messages[3]
    request = ModelRequest(
        system=original.system,
        messages=tuple(messages),
        tools=original.tools,
        max_tokens=original.max_tokens,
    )
    client, _ = install_fake_client(monkeypatch, FakeStream(text_response_events()))
    model = AnthropicMessagesModel(ModelSettings(model="model-a"), api_key="secret")

    await model.complete(request, lambda _: None, CancellationToken())

    wire_messages = client.messages.calls[0]["messages"]
    assert isinstance(wire_messages, list)
    result_message = wire_messages[2]
    assert isinstance(result_message, dict)
    assert [block["tool_use_id"] for block in result_message["content"]] == [
        "tool-1",
        "tool-2",
    ]


@pytest.mark.asyncio
async def test_duplicate_tool_result_id_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duplicate result must not satisfy the required complete ID set."""
    original = request_with_tool_history()
    messages = list(original.messages)
    messages[4] = messages[3]
    request = ModelRequest(
        system=original.system,
        messages=tuple(messages),
        tools=original.tools,
        max_tokens=original.max_tokens,
    )
    client, _ = install_fake_client(monkeypatch, FakeStream(text_response_events()))
    model = AnthropicMessagesModel(ModelSettings(model="model-a"), api_key="secret")

    with pytest.raises(ModelProtocolError) as raised:
        await model.complete(request, lambda _: None, CancellationToken())

    assert raised.value.code == "DUPLICATE_TOOL_RESULT_ID"
    assert client.messages.calls == []


def status_error(
    status: int,
    body: object,
    *,
    headers: dict[str, str] | None = None,
    message: str = "provider rejected request",
) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://provider.example/v1/messages")
    response = httpx2.Response(status, request=request, headers=headers)
    return anthropic.APIStatusError(message, response=response, body=body)


@pytest.mark.asyncio
async def test_structured_context_error_maps_to_compression_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Depending on mutable human prose would miss compatible structured overflow errors."""
    error = status_error(
        400,
        {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
                "message": "unrelated prose",
            },
        },
    )
    install_fake_client(monkeypatch, error)
    model = AnthropicMessagesModel(ModelSettings(model="model-a"), api_key="secret")

    with pytest.raises(ModelAPIError) as raised:
        await model.complete(request_with_tool_history(), lambda _: None, CancellationToken())

    assert raised.value.status_code == 400
    assert raised.value.error_type == "context_too_large"
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_error_message_text_cannot_turn_another_400_into_context_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Natural-language matching could compress or retry an unrelated invalid request."""
    error = status_error(
        400,
        {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "context too large maybe",
            },
        },
    )
    install_fake_client(monkeypatch, error)
    model = AnthropicMessagesModel(ModelSettings(model="model-a"), api_key="secret")

    with pytest.raises(ModelAPIError) as raised:
        await model.complete(request_with_tool_history(), lambda _: None, CancellationToken())

    assert raised.value.error_type == "invalid_request_error"
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_status_retry_metadata_comes_from_status_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignoring provider retry controls would make the later retry owner misclassify calls."""
    error = status_error(
        429,
        {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}},
        headers={"x-should-retry": "false", "retry-after": "7"},
    )
    install_fake_client(monkeypatch, error)
    model = AnthropicMessagesModel(ModelSettings(model="model-a"), api_key="secret")

    with pytest.raises(ModelAPIError) as raised:
        await model.complete(request_with_tool_history(), lambda _: None, CancellationToken())

    assert raised.value.error_type == "rate_limit_error"
    assert raised.value.retry_after == "7"
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_x_should_retry_true_overrides_the_default_status_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignoring the explicit provider override would classify a retryable compatible error wrong."""
    error = status_error(
        400,
        {"type": "error", "error": {"type": "invalid_request_error"}},
        headers={"x-should-retry": "true", "retry-after-ms": "2500", "retry-after": "7"},
    )
    install_fake_client(monkeypatch, error)
    model = AnthropicMessagesModel(ModelSettings(model="model-a"), api_key="secret")

    with pytest.raises(ModelAPIError) as raised:
        await model.complete(request_with_tool_history(), lambda _: None, CancellationToken())

    assert raised.value.retryable is True
    assert raised.value.retry_after_ms == "2500"
    assert raised.value.retry_after == "7"


@pytest.mark.asyncio
async def test_sse_overloaded_error_with_http_200_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Classifying an SSE error by HTTP 200 alone would suppress its required retry."""
    error = status_error(
        200,
        {
            "type": "error",
            "error": {"type": "overloaded_error", "message": "structured only"},
        },
    )
    stream = FailingStream(error)
    install_fake_client(monkeypatch, stream)
    model = AnthropicMessagesModel(ModelSettings(model="model-a"), api_key="secret")

    with pytest.raises(ModelAPIError) as raised:
        await model.complete(request_with_tool_history(), lambda _: None, CancellationToken())

    assert raised.value.status_code == 200
    assert raised.value.error_type == "overloaded_error"
    assert raised.value.retryable is True
    assert stream.closed is True


@pytest.mark.asyncio
async def test_connection_failures_are_typed_and_not_retried_by_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Adapter must expose retryability without owning a retry loop."""
    request = httpx2.Request("POST", "https://provider.example/v1/messages")
    client, _ = install_fake_client(monkeypatch, anthropic.APIConnectionError(request=request))
    model = AnthropicMessagesModel(ModelSettings(model="model-a"), api_key="secret")

    with pytest.raises(ModelTransportError) as raised:
        await model.complete(request_with_tool_history(), lambda _: None, CancellationToken())

    assert raised.value.retryable is True
    assert len(client.messages.calls) == 1


@pytest.mark.asyncio
async def test_read_timeouts_are_typed_as_retryable_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leaving timeouts non-retryable would bypass the request retry policy."""
    request = httpx2.Request("POST", "https://provider.example/v1/messages")
    install_fake_client(monkeypatch, anthropic.APITimeoutError(request=request))
    model = AnthropicMessagesModel(ModelSettings(model="model-a"), api_key="secret")

    with pytest.raises(ModelTransportError) as raised:
        await model.complete(request_with_tool_history(), lambda _: None, CancellationToken())

    assert raised.value.retryable is True


@pytest.mark.asyncio
async def test_cancellation_closes_an_in_flight_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling only between frames would leave a silent stream alive after Stop."""
    stream = BlockingStream()
    install_fake_client(monkeypatch, stream)
    model = AnthropicMessagesModel(ModelSettings(model="model-a"), api_key="secret")
    cancellation = CancellationToken()
    task = asyncio.create_task(
        model.complete(request_with_tool_history(), lambda _: None, cancellation)
    )
    await stream.started.wait()

    cancellation.cancel()

    with pytest.raises(CancellationRequested):
        await asyncio.wait_for(task, timeout=1)
    assert stream.closed is True


@pytest.mark.asyncio
async def test_cancellation_interrupts_request_before_stream_is_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Waiting only for SSE frames would leave the initial HTTP request running after Stop."""
    messages = BlockingMessages()
    client = FakeClient(FakeStream([]))
    client.messages = messages  # type: ignore[assignment]
    monkeypatch.setattr(anthropic_messages, "AsyncAnthropic", lambda **_: client)
    model = AnthropicMessagesModel(ModelSettings(model="model-a"), api_key="secret")
    cancellation = CancellationToken()
    task = asyncio.create_task(
        model.complete(request_with_tool_history(), lambda _: None, cancellation)
    )
    await messages.started.wait()

    cancellation.cancel()

    with pytest.raises(CancellationRequested):
        await asyncio.wait_for(task, timeout=1)


def test_model_message_rejects_provider_only_roles() -> None:
    """Allowing system/tool wire roles would leak provider representation into Core."""
    with pytest.raises(ValueError, match="role"):
        ModelMessage(role="system", parts=(TextPart("bad"),))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_scripted_model_implements_gateway_without_production_switches() -> None:
    """A fake that diverges from ModelGateway would make later loop tests misleading."""
    from tests.fakes.model import ScriptedModel

    expected = AssistantTurn(
        id="scripted-1",
        parts=(TextPart("scripted"),),
        stop_reason=ModelStopReason.END_TURN,
        usage=Usage(input_tokens=None, output_tokens=None),
    )
    model = ScriptedModel([expected])
    deltas: list[TextDelta] = []

    actual = await model.complete(request_with_tool_history(), deltas.append, CancellationToken())

    assert actual is expected
    assert model.requests == [request_with_tool_history()]
    assert deltas == [TextDelta(index=0, text="scripted")]
