"""Strict JSON contracts shared by the REST and WebSocket adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from coding_agent.core.models import (
    ApprovalDecision,
    ApprovalStatus,
    DurableEvent,
    ErrorKind,
    Message,
    MessageStatus,
    PendingApproval,
    Run,
    RunState,
    Session,
    SessionSnapshot,
    StopReason,
    TextPart,
    ToolExecution,
    ToolExecutionState,
    ToolResult,
    ToolUsePart,
)
from coding_agent.runtime.publisher import AssistantDelta, ToolOutputDelta


class StrictDto(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthDto(StrictDto):
    status: Literal["ok"] = "ok"


class BootstrapDto(StrictDto):
    csrf_token: str
    websocket_url: str


class DirectoryEntryDto(StrictDto):
    name: str
    path: str


class DirectoryListingDto(StrictDto):
    path: str
    directories: list[DirectoryEntryDto]


class CreateSessionRequest(StrictDto):
    workspace: str
    title: str | None = None


class SessionDto(StrictDto):
    id: str
    title: str | None
    workspace_realpath: str
    requires_recovery_ack: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, session: Session) -> SessionDto:
        return cls(
            id=session.id,
            title=session.title,
            workspace_realpath=session.workspace_realpath,
            requires_recovery_ack=session.requires_recovery_ack,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )


class RunDto(StrictDto):
    id: str
    session_id: str
    state: RunState
    stop_reason: StopReason | None
    error_kind: ErrorKind | None
    cancellation_requested_at: datetime | None
    config_snapshot: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None

    @classmethod
    def from_domain(cls, run: Run) -> RunDto:
        return cls(
            id=run.id,
            session_id=run.session_id,
            state=run.state,
            stop_reason=run.stop_reason,
            error_kind=run.error_kind,
            cancellation_requested_at=run.cancellation_requested_at,
            config_snapshot=dict(run.config_snapshot),
            started_at=run.started_at,
            finished_at=run.finished_at,
        )


class TextPartDto(StrictDto):
    type: Literal["text"] = "text"
    text: str


class ToolUsePartDto(StrictDto):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolErrorDto(StrictDto):
    code: str
    message: str


class ToolResultPartDto(StrictDto):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    content: str
    ok: bool
    error: ToolErrorDto | None
    data: dict[str, Any]
    truncated: bool


MessagePartDto = Annotated[
    TextPartDto | ToolUsePartDto | ToolResultPartDto,
    Field(discriminator="type"),
]


class MessageDto(StrictDto):
    id: str
    session_id: str
    run_id: str | None
    seq: int
    role: str
    parts: list[MessagePartDto]
    status: MessageStatus
    tool_call_id: str | None

    @classmethod
    def from_domain(cls, message: Message) -> MessageDto:
        parts: list[TextPartDto | ToolUsePartDto | ToolResultPartDto] = []
        for part in message.parts:
            if isinstance(part, TextPart):
                parts.append(TextPartDto(text=part.text))
            elif isinstance(part, ToolUsePart):
                parts.append(
                    ToolUsePartDto(
                        id=part.call.id,
                        name=part.call.name,
                        input=dict(part.call.input),
                    )
                )
            elif isinstance(part, ToolResult):
                parts.append(
                    ToolResultPartDto(
                        tool_call_id=part.tool_call_id,
                        content=part.content,
                        ok=part.ok,
                        error=(
                            ToolErrorDto(code=part.error.code, message=part.error.message)
                            if part.error is not None
                            else None
                        ),
                        data=dict(part.data),
                        truncated=part.truncated,
                    )
                )
            else:  # pragma: no cover - the closed domain union makes this defensive only.
                raise TypeError(f"unsupported message part: {type(part).__name__}")
        return cls(
            id=message.id,
            session_id=message.session_id,
            run_id=message.run_id,
            seq=message.seq,
            role=message.role,
            parts=parts,
            status=message.status,
            tool_call_id=message.tool_call_id,
        )


class ToolExecutionDto(StrictDto):
    tool_call_id: str
    run_id: str
    assistant_message_id: str
    call_order: int
    name: str
    input: dict[str, Any]
    requires_approval: bool
    approval_status: ApprovalStatus
    approval_decision: ApprovalDecision | None
    approval_decided_at: datetime | None
    execution_state: ToolExecutionState
    result: ToolResultPartDto | None
    duration_ms: int | None

    @classmethod
    def from_domain(cls, execution: ToolExecution) -> ToolExecutionDto:
        result = execution.result
        return cls(
            tool_call_id=execution.tool_call_id,
            run_id=execution.run_id,
            assistant_message_id=execution.assistant_message_id,
            call_order=execution.call_order,
            name=execution.name,
            input=dict(execution.input),
            requires_approval=execution.requires_approval,
            approval_status=execution.approval_status,
            approval_decision=execution.approval_decision,
            approval_decided_at=execution.approval_decided_at,
            execution_state=execution.execution_state,
            result=(
                ToolResultPartDto(
                    tool_call_id=result.tool_call_id,
                    content=result.content,
                    ok=result.ok,
                    error=(
                        ToolErrorDto(code=result.error.code, message=result.error.message)
                        if result.error is not None
                        else None
                    ),
                    data=dict(result.data),
                    truncated=result.truncated,
                )
                if result is not None
                else None
            ),
            duration_ms=execution.duration_ms,
        )


class PendingApprovalDto(StrictDto):
    run_id: str
    tool_call_id: str
    name: str
    input: dict[str, Any]
    target: str | None
    preview: str | None
    metadata: dict[str, Any]

    @classmethod
    def from_domain(cls, approval: PendingApproval) -> PendingApprovalDto:
        return cls(
            run_id=approval.run_id,
            tool_call_id=approval.tool_call_id,
            name=approval.name,
            input=dict(approval.input),
            target=approval.target,
            preview=approval.preview,
            metadata=dict(approval.metadata),
        )


class InterruptedBannerDto(StrictDto):
    run_id: str
    stop_reason: StopReason
    requires_recovery_ack: bool


class SessionSnapshotDto(StrictDto):
    session: SessionDto
    active_run: RunDto | None
    messages: list[MessageDto]
    tools: list[ToolExecutionDto]
    pending_approval: PendingApprovalDto | None
    interrupted_banner: InterruptedBannerDto | None
    snapshot_seq: int

    @classmethod
    def from_domain(cls, snapshot: SessionSnapshot) -> SessionSnapshotDto:
        return cls(
            session=SessionDto.from_domain(snapshot.session),
            active_run=(
                RunDto.from_domain(snapshot.active_run) if snapshot.active_run is not None else None
            ),
            messages=[MessageDto.from_domain(message) for message in snapshot.messages],
            tools=[ToolExecutionDto.from_domain(tool) for tool in snapshot.tools],
            pending_approval=(
                PendingApprovalDto.from_domain(snapshot.pending_approval)
                if snapshot.pending_approval is not None
                else None
            ),
            interrupted_banner=(
                InterruptedBannerDto(
                    run_id=snapshot.interrupted_banner.run_id,
                    stop_reason=snapshot.interrupted_banner.stop_reason,
                    requires_recovery_ack=snapshot.interrupted_banner.requires_recovery_ack,
                )
                if snapshot.interrupted_banner is not None
                else None
            ),
            snapshot_seq=snapshot.snapshot_seq,
        )


class EmptyPayload(StrictDto):
    pass


class RunStartPayload(StrictDto):
    content: str

    @field_validator("content")
    @classmethod
    def require_nonempty_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class RunStopPayload(StrictDto):
    run_id: str = Field(min_length=1)


class ApprovalResolvePayload(StrictDto):
    run_id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    decision: ApprovalDecision


class SessionSubscribeCommand(StrictDto):
    type: Literal["session.subscribe"]
    client_command_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    payload: EmptyPayload


class RunStartCommand(StrictDto):
    type: Literal["run.start"]
    client_command_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    payload: RunStartPayload


class RunStopCommand(StrictDto):
    type: Literal["run.stop"]
    client_command_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    payload: RunStopPayload


class ApprovalResolveCommand(StrictDto):
    type: Literal["approval.resolve"]
    client_command_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    payload: ApprovalResolvePayload


class SessionAckRecoveryCommand(StrictDto):
    type: Literal["session.ack_recovery"]
    client_command_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    payload: EmptyPayload


ClientCommand: TypeAlias = Annotated[
    SessionSubscribeCommand
    | RunStartCommand
    | RunStopCommand
    | ApprovalResolveCommand
    | SessionAckRecoveryCommand,
    Field(discriminator="type"),
]


class AckEnvelope(StrictDto):
    type: Literal["ack"] = "ack"
    client_command_id: str
    session_id: str
    command_type: str
    status: Literal["completed"] = "completed"
    resource_id: str


class CommandErrorEnvelope(StrictDto):
    type: Literal["command_error"] = "command_error"
    client_command_id: str | None
    session_id: str | None
    code: str
    message: str


class SnapshotEnvelope(StrictDto):
    type: Literal["snapshot"] = "snapshot"
    client_command_id: str
    session_id: str
    snapshot: SessionSnapshotDto


class DurableEventDto(StrictDto):
    seq: int
    session_id: str
    run_id: str | None
    type: str
    payload: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_domain(cls, event: DurableEvent) -> DurableEventDto:
        return cls(
            seq=event.seq,
            session_id=event.session_id,
            run_id=event.run_id,
            type=event.type,
            payload=dict(event.payload),
            created_at=event.created_at,
        )


class DurableEnvelope(StrictDto):
    type: Literal["durable"] = "durable"
    event: DurableEventDto


class AssistantDeltaEnvelope(StrictDto):
    type: Literal["assistant.delta"] = "assistant.delta"
    session_id: str
    run_id: str
    draft_epoch: str
    index: int
    text: str

    @classmethod
    def from_domain(cls, delta: AssistantDelta) -> AssistantDeltaEnvelope:
        return cls(
            session_id=delta.session_id,
            run_id=delta.run_id,
            draft_epoch=delta.draft_epoch,
            index=delta.index,
            text=delta.text,
        )


class ToolOutputDeltaEnvelope(StrictDto):
    type: Literal["tool.output.delta"] = "tool.output.delta"
    session_id: str
    run_id: str
    draft_epoch: str
    tool_call_id: str
    text: str

    @classmethod
    def from_domain(cls, delta: ToolOutputDelta) -> ToolOutputDeltaEnvelope:
        return cls(
            session_id=delta.session_id,
            run_id=delta.run_id,
            draft_epoch=delta.draft_epoch,
            tool_call_id=delta.tool_call_id,
            text=delta.text,
        )


ServerMessage: TypeAlias = (
    AckEnvelope
    | CommandErrorEnvelope
    | SnapshotEnvelope
    | DurableEnvelope
    | AssistantDeltaEnvelope
    | ToolOutputDeltaEnvelope
)


__all__ = [
    "AckEnvelope",
    "ApprovalResolveCommand",
    "AssistantDeltaEnvelope",
    "BootstrapDto",
    "ClientCommand",
    "CommandErrorEnvelope",
    "CreateSessionRequest",
    "DirectoryEntryDto",
    "DirectoryListingDto",
    "DurableEnvelope",
    "DurableEventDto",
    "HealthDto",
    "MessageDto",
    "PendingApprovalDto",
    "RunDto",
    "RunStartCommand",
    "RunStopCommand",
    "ServerMessage",
    "SessionAckRecoveryCommand",
    "SessionDto",
    "SessionSubscribeCommand",
    "SessionSnapshotDto",
    "StrictDto",
    "ToolExecutionDto",
    "ToolOutputDeltaEnvelope",
]
