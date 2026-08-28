"""Stable, provider-independent contracts at the model boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.models import (
    AssistantTurn,
    FrozenJsonMapping,
    MessagePart,
    TextPart,
    ToolResult,
    ToolUsePart,
    Usage,
    _freeze_json,
)


@dataclass(frozen=True, slots=True)
class TextDelta:
    index: int
    text: str

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("text delta index must not be negative")


DeltaSink: TypeAlias = Callable[[TextDelta], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: Literal["user", "assistant"]
    parts: tuple[MessagePart, ...]

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant"}:
            raise ValueError("model message role must be user or assistant")
        parts = tuple(self.parts)
        if not parts:
            raise ValueError("model message parts must not be empty")
        if self.role == "assistant" and any(
            not isinstance(part, TextPart | ToolUsePart) for part in parts
        ):
            raise ValueError("assistant model messages may only contain text or tool use")
        if self.role == "user" and any(
            not isinstance(part, TextPart | ToolResult) for part in parts
        ):
            raise ValueError("user model messages may only contain text or tool results")
        object.__setattr__(self, "parts", parts)


@dataclass(frozen=True, slots=True)
class ModelRequest:
    system: str
    messages: tuple[ModelMessage, ...]
    tools: tuple[FrozenJsonMapping, ...]
    max_tokens: int

    def __post_init__(self) -> None:
        if not isinstance(self.system, str):
            raise TypeError("model request system must be a string")
        if isinstance(self.max_tokens, bool) or self.max_tokens <= 0:
            raise ValueError("model request max_tokens must be a positive integer")
        object.__setattr__(self, "messages", tuple(self.messages))
        frozen_tools: list[FrozenJsonMapping] = []
        for tool in self.tools:
            frozen = _freeze_json(tool)
            if not isinstance(frozen, Mapping):
                raise TypeError("model tool schema must be a JSON object")
            frozen_tools.append(frozen)
        object.__setattr__(self, "tools", tuple(frozen_tools))


class ModelGateway(Protocol):
    async def complete(
        self,
        request: ModelRequest,
        on_text_delta: DeltaSink,
        cancellation: CancellationToken,
    ) -> AssistantTurn: ...


class ModelTransportError(RuntimeError):
    def __init__(self, retryable: bool, cause: BaseException) -> None:
        super().__init__(f"model transport failure: {type(cause).__name__}")
        self.retryable = retryable
        self.cause = cause


class ModelAPIError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        error_type: str | None,
        retry_after: str | None,
        retryable: bool,
        *,
        retry_after_ms: str | None = None,
    ) -> None:
        super().__init__(f"model API failure: status={status_code}, type={error_type or 'unknown'}")
        self.status_code = status_code
        self.error_type = error_type
        self.retry_after = retry_after
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms


class ModelProtocolError(RuntimeError):
    def __init__(self, code: str, detail: str, *, usage: Usage | None = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.usage = usage


__all__ = [
    "DeltaSink",
    "ModelAPIError",
    "ModelGateway",
    "ModelMessage",
    "ModelProtocolError",
    "ModelRequest",
    "ModelTransportError",
    "TextDelta",
    "Usage",
]
