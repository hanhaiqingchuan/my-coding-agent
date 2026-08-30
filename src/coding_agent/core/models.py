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
    INTERNAL_ERROR = "internal_error"
    """A failure inside this process, not in the operator's configuration or the model.

    Spec 5.2 fixes a minimum vocabulary rather than a closed one. Reporting a local
    persistence or environment failure as ``CONFIG_ERROR`` would send the user to repair
    settings that are already correct.
    """


class ErrorKind(StrEnum):
    MODEL_PROTOCOL_ERROR = "model_protocol_error"
    AUTH_ERROR = "auth_error"
    CONFIG_ERROR = "config_error"
    RETRY_EXHAUSTED = "retry_exhausted"
    CONTEXT_OVERFLOW = "context_overflow"
    INTERNAL_ERROR = "internal_error"


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
class ThinkingPart:
    """Provider reasoning text, kept for display only.

    Spec 8.2: reasoning blocks are aggregated in block order into this part, persist like
    any other part, and are shown collapsed in the UI — but they never enter the model
    view, are never echoed back to the provider, and never drive loop state transitions.
    """

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


AssistantPart: TypeAlias = TextPart | ThinkingPart | ToolUsePart
MessagePart: TypeAlias = TextPart | ThinkingPart | ToolUsePart | ToolResult


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    id: str
    parts: tuple[AssistantPart, ...]
    stop_reason: ModelStopReason
    usage: Usage
    invalid_tool_arguments: Mapping[str, ToolError] = field(default_factory=dict)
    """Calls whose arguments arrived complete but unusable, keyed by tool call id.

    Spec 8.3 keeps such a call correctable: the identity is valid, so the loop commits a
    tool error result for it and lets the model fix its own arguments. A flagged call
    carries no usable input and must never be prepared or executed.
    """

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("assistant turn id must not be empty")
        parts = tuple(self.parts)
        call_ids = [part.call.id for part in parts if isinstance(part, ToolUsePart)]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("tool call ids must be unique within an assistant turn")
        invalid_tool_arguments = dict(self.invalid_tool_arguments)
        if not set(invalid_tool_arguments).issubset(call_ids):
            raise ValueError("invalid tool arguments must reference a tool call in this turn")
        if not all(isinstance(error, ToolError) for error in invalid_tool_arguments.values()):
            raise ValueError("invalid tool arguments must carry a stable ToolError")
        object.__setattr__(self, "parts", parts)
        object.__setattr__(self, "invalid_tool_arguments", MappingProxyType(invalid_tool_arguments))

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
    auto_approve: bool = False
    """Per-session approval mode (spec 13.4): ``True`` auto-approves writes/commands.

    The persisted flag survives reloads and restarts; a process-level ``--yes`` flag
    stays stronger than it, so the session flag can only widen auto-approval.
    """


@dataclass(frozen=True, slots=True)
class ContextLoad:
    """What the focus run actually loaded into its system context (spec 13.5).

    ``agents_md_path`` is the workspace-relative file the run-start scan read (``None``
    when the workspace has none); ``skills_read`` lists only skills the model pulled
    through the ``skill`` tool in that run, never the discovered index.
    """

    agents_md_path: str | None
    skills_read: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.agents_md_path is not None and not self.agents_md_path:
            raise ValueError("agents_md_path must be a non-empty path or None")
        object.__setattr__(self, "skills_read", tuple(self.skills_read))


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
class RunContextEstimate:
    """The latest context-window projection of one run, for the UI progress bar.

    ``estimated_tokens`` is what the context builder estimated for the most recent
    model view; ``available_tokens`` is the input budget that view had to fit
    (spec 7.1), and the remaining fields are the static settings that produced the
    budget. This is evidence about the last build, not a provider usage report and
    not a token-count guarantee (spec 7.1 keeps the estimate heuristic).
    """

    estimated_tokens: int
    available_tokens: int
    window_tokens: int
    max_output_tokens: int
    safety_margin_tokens: int

    def __post_init__(self) -> None:
        for name, value in (
            ("estimated_tokens", self.estimated_tokens),
            ("available_tokens", self.available_tokens),
            ("window_tokens", self.window_tokens),
            ("max_output_tokens", self.max_output_tokens),
            ("safety_margin_tokens", self.safety_margin_tokens),
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
    context: RunContextEstimate | None = None
    """The run's latest context estimate; ``None`` until the loop's first build."""

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
    last_finished_run: Run | None = None
    """The session's most recently finished run.

    ``active_run`` stays strictly non-terminal, so every ``stop_reason`` is written in the
    same statement that makes a run terminal and would otherwise never reach the browser.
    This field keeps that finished record available to the run panel spec 13 describes.
    """
    context_load: ContextLoad | None = None
    """Read-only projection of what the focus run (active, else last finished) loaded.

    Spec 13.5's context panel needs the AGENTS.md path and the skills the model actually
    read; both derive from durable evidence the run already produced, never discovery.
    """

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
