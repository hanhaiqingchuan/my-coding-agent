"""Immutable, vendor-neutral values exchanged by the agent's core layers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias


class RunState(StrEnum):
    STARTING = "starting"
    BUILDING_CONTEXT = "building_context"
    COMPACTING = "compacting"
    MODEL_STREAMING = "model_streaming"
    RETRY_WAIT = "retry_wait"
    AWAITING_APPROVAL = "awaiting_approval"
    TOOL_RUNNING = "tool_running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    STOPPED = "stopped"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class StopReason(StrEnum):
    COMPLETED = "completed"
    USER_STOP = "user_stop"
    MAX_ROUNDS = "max_rounds"
    DOOM_LOOP = "doom_loop"
    EMPTY_RESPONSE = "empty_response"
    OUTPUT_TRUNCATED = "output_truncated"
    INCOMPLETE_TOOL_CALL = "incomplete_tool_call"
    AUTH_ERROR = "auth_error"
    CONFIG_ERROR = "config_error"
    RETRY_EXHAUSTED = "retry_exhausted"
    CONTEXT_OVERFLOW = "context_overflow"
    MODEL_REFUSAL = "model_refusal"
    PAUSE_TURN = "pause_turn"
    SERVER_RESTART = "server_restart"
    MODEL_PROTOCOL_ERROR = "model_protocol_error"


class ErrorKind(StrEnum):
    MODEL_PROTOCOL_ERROR = "model_protocol_error"
    AUTH_ERROR = "auth_error"
    CONFIG_ERROR = "config_error"
    RETRY_EXHAUSTED = "retry_exhausted"
    CONTEXT_OVERFLOW = "context_overflow"


class MessageStatus(StrEnum):
    PENDING_TOOLS = "pending_tools"
    COMMITTED = "committed"
    INTERRUPTED = "interrupted"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ToolExecutionState(StrEnum):
    QUEUED = "queued"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class EffectStartResult(StrEnum):
    STARTED = "started"
    CANCELLING = "cancelling"
    NOT_ACTIVE = "not_active"


class ModelStopReason(StrEnum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    PAUSE_TURN = "pause_turn"
    REFUSAL = "refusal"
    MODEL_CONTEXT_WINDOW_EXCEEDED = "model_context_window_exceeded"


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
FrozenJsonMapping: TypeAlias = Mapping[str, JsonValue]
UnifiedDiffPreview: TypeAlias = str


def _freeze_json(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    if value is None or type(value) in {str, int, float, bool}:
        return value
    raise TypeError(f"expected a JSON value, got {type(value).__name__}")


def _freeze_mapping(value: Mapping[object, object]) -> FrozenJsonMapping:
    frozen: dict[str, JsonValue] = {}
    for key, item in value.items():
        if type(key) is not str:
            raise TypeError(f"expected a JSON object key, got {type(key).__name__}")
        frozen[key] = _freeze_json(item)
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cache_creation_input_tokens", self.cache_creation_input_tokens),
            ("cache_read_input_tokens", self.cache_read_input_tokens),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or None")


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    input: FrozenJsonMapping

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("tool call id must not be empty")
        if not self.name:
            raise ValueError("tool call name must not be empty")
        object.__setattr__(self, "input", _freeze_mapping(self.input))


@dataclass(frozen=True, slots=True)
class ToolUsePart:
    call: ToolCall


@dataclass(frozen=True, slots=True)
class ToolError:
    """A stable, machine-readable tool error with optional human-facing detail."""

    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("tool error code must not be empty")


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    content: str
    ok: bool
    error: ToolError | None = None
    data: FrozenJsonMapping = field(default_factory=dict)
    truncated: bool = False

    def __post_init__(self) -> None:
        if not self.tool_call_id:
            raise ValueError("tool result requires a tool call id")
        if self.ok and self.error is not None:
            raise ValueError("a successful tool result must not have an error")
        if not self.ok and self.error is None:
            raise ValueError("a failed tool result requires a stable error")
        if not isinstance(self.truncated, bool):
            raise ValueError("tool result truncated must be a boolean")
        object.__setattr__(self, "data", _freeze_mapping(self.data))


AssistantPart: TypeAlias = TextPart | ToolUsePart
MessagePart: TypeAlias = TextPart | ToolUsePart | ToolResult


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    id: str
    parts: tuple[AssistantPart, ...]
    stop_reason: ModelStopReason
    usage: Usage

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("assistant turn id must not be empty")
        parts = tuple(self.parts)
        call_ids = [part.call.id for part in parts if isinstance(part, ToolUsePart)]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("tool call ids must be unique within an assistant turn")
        object.__setattr__(self, "parts", parts)

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        """Return calls without changing the canonical interleaved parts sequence."""
        return tuple(part.call for part in self.parts if isinstance(part, ToolUsePart))


@dataclass(frozen=True, slots=True)
class Message:
    id: str
    session_id: str
    run_id: str | None
    seq: int
    role: str
    parts: tuple[MessagePart, ...]
    status: MessageStatus
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.session_id:
            raise ValueError("message id and session id must not be empty")
        if self.seq < 0:
            raise ValueError("message sequence must not be negative")
        object.__setattr__(self, "parts", tuple(self.parts))


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    run_id: str
    tool_call_id: str
    status: ApprovalStatus
    decision: ApprovalDecision | None
    decided_at: datetime | None


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    call: ToolCall
    requires_approval: bool
    target: str | None = None
    preview: UnifiedDiffPreview | None = None
    baseline_sha256: str | None = None
    metadata: FrozenJsonMapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class PendingToolGroup:
    id: str
    run_id: str
    assistant_message_id: str
    calls: tuple[PreparedToolCall, ...]

    def __post_init__(self) -> None:
        calls = tuple(self.calls)
        call_ids = [call.call.id for call in calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("pending tool group requires unique tool call ids")
        object.__setattr__(self, "calls", calls)


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    session_id: str
    covered_through_message_seq: int
    summary: str
    created_at: datetime
    version: int = 1
    source_event_ids: tuple[str, ...] = ()
    model: str = ""
    estimator_id: str = ""
    token_estimate: int = 0
    compaction_above_target: bool = False

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("context snapshot session id must not be empty")
        if self.covered_through_message_seq < 0:
            raise ValueError("covered message sequence must not be negative")
        if self.version < 1:
            raise ValueError("context snapshot version must be positive")
        source_event_ids = tuple(self.source_event_ids)
        if any(not event_id for event_id in source_event_ids):
            raise ValueError("context snapshot source event ids must not be empty")
        if len(source_event_ids) != len(set(source_event_ids)):
            raise ValueError("context snapshot source event ids must be unique")
        if self.token_estimate < 0:
            raise ValueError("context snapshot token estimate must not be negative")
        if not isinstance(self.compaction_above_target, bool):
            raise ValueError("compaction_above_target must be a boolean")
        object.__setattr__(self, "source_event_ids", source_event_ids)


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    title: str | None
    workspace_realpath: str
    requires_recovery_ack: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RunTotals:
    """Cumulative counters the ``runs`` row accumulates over a run's whole lifetime.

    The token fields are sums of the per-request usage the provider actually reported;
    requests that reported no usage contribute nothing, so these are totals of *known*
    usage rather than a per-request measurement. Per-request null preservation lives in
    ``model_requests`` and the evaluation report, which is why no ``usage_source`` belongs
    here. A sum across rounds also cannot express current context-window occupancy.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    round_count: int = 0
    retry_count: int = 0

    def __post_init__(self) -> None:
        for name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cache_creation_input_tokens", self.cache_creation_input_tokens),
            ("cache_read_input_tokens", self.cache_read_input_tokens),
            ("round_count", self.round_count),
            ("retry_count", self.retry_count),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    session_id: str
    state: RunState
    stop_reason: StopReason | None
    error_kind: ErrorKind | None
    cancellation_requested_at: datetime | None
    config_snapshot: FrozenJsonMapping
    started_at: datetime
    finished_at: datetime | None
    totals: RunTotals = field(default_factory=RunTotals)

    def __post_init__(self) -> None:
        object.__setattr__(self, "config_snapshot", _freeze_mapping(self.config_snapshot))


@dataclass(frozen=True, slots=True)
class ToolExecution:
    tool_call_id: str
    run_id: str
    assistant_message_id: str
    call_order: int
    name: str
    input: FrozenJsonMapping
    requires_approval: bool
    approval_status: ApprovalStatus
    approval_decision: ApprovalDecision | None
    approval_decided_at: datetime | None
    execution_state: ToolExecutionState
    result: ToolResult | None
    duration_ms: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "input", _freeze_mapping(self.input))


@dataclass(frozen=True, slots=True)
class PendingApproval:
    run_id: str
    tool_call_id: str
    name: str
    input: FrozenJsonMapping
    target: str | None
    preview: str | None
    metadata: FrozenJsonMapping

    def __post_init__(self) -> None:
        object.__setattr__(self, "input", _freeze_mapping(self.input))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class InterruptedRunNotice:
    run_id: str
    stop_reason: StopReason
    requires_recovery_ack: bool


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    session: Session
    active_run: Run | None
    messages: tuple[Message, ...]
    snapshot_seq: int
    tools: tuple[ToolExecution, ...] = ()
    pending_approval: PendingApproval | None = None
    interrupted_banner: InterruptedRunNotice | None = None

    def __post_init__(self) -> None:
        if self.snapshot_seq < 0:
            raise ValueError("snapshot sequence must not be negative")
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "tools", tuple(self.tools))


@dataclass(frozen=True, slots=True)
class ClientCommandRecord:
    client_command_id: str
    session_id: str
    command_type: str
    payload_hash: str
    run_id: str | None
    received_at: datetime


@dataclass(frozen=True, slots=True)
class DurableEvent:
    seq: int
    session_id: str
    run_id: str | None
    type: str
    payload: FrozenJsonMapping
    created_at: datetime

    def __post_init__(self) -> None:
        if self.seq < 0:
            raise ValueError("durable event sequence must not be negative")
        object.__setattr__(self, "payload", _freeze_mapping(self.payload))
