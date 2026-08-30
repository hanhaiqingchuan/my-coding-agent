"""Authenticated WebSocket command and event delivery."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket
from pydantic import TypeAdapter, ValidationError
from starlette.websockets import WebSocketDisconnect

from coding_agent.api.dependencies import ApiDependencies
from coding_agent.api.schemas import (
    AckEnvelope,
    ApprovalResolveCommand,
    AssistantDeltaEnvelope,
    AssistantThinkingClosedEnvelope,
    AssistantThinkingDeltaEnvelope,
    ClientCommand,
    CommandErrorEnvelope,
    DurableEnvelope,
    DurableEventDto,
    RunStartCommand,
    RunStopCommand,
    SessionAckRecoveryCommand,
    SessionClearCommand,
    SessionCompactCommand,
    SessionSetApprovalModeCommand,
    SessionSnapshotDto,
    SessionSubscribeCommand,
    SnapshotEnvelope,
    StrictDto,
    ToolOutputDeltaEnvelope,
)
from coding_agent.core.errors import StoreError
from coding_agent.core.models import DurableEvent
from coding_agent.runtime.publisher import (
    AssistantDelta,
    AssistantThinkingClosed,
    AssistantThinkingDelta,
    EventSubscription,
    SubscriptionOverflow,
    ToolOutputDelta,
)

WS_CLOSE_AUTH_EXPIRED = 4401
WS_CLOSE_FORBIDDEN = 4403
WS_CLOSE_SUBSCRIBER_OVERFLOW = 4408
WS_SUBPROTOCOL = "coding-agent"

router = APIRouter()
_COMMAND_ADAPTER = TypeAdapter(ClientCommand)


@router.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    dependencies: ApiDependencies = websocket.app.state.api_dependencies
    if websocket.headers.get("host") not in dependencies.allowed_hosts:
        await _accept_then_close(websocket, WS_CLOSE_FORBIDDEN, "Host is not allowed")
        return
    if not dependencies.origin_allowed(
        websocket.headers.get("origin"),
        host=websocket.headers.get("host"),
        scheme=websocket.scope.get("scheme", "ws"),
    ):
        await _accept_then_close(websocket, WS_CLOSE_FORBIDDEN, "Origin is not allowed")
        return
    protocols = websocket.scope.get("subprotocols", [])
    if (
        not isinstance(protocols, list)
        or len(protocols) != 2
        or protocols[0] != WS_SUBPROTOCOL
        or not dependencies.token_matches(protocols[1])
    ):
        await _accept_then_close(websocket, WS_CLOSE_AUTH_EXPIRED, "Process token is invalid")
        return

    await websocket.accept(subprotocol=WS_SUBPROTOCOL)
    connection = _SessionConnection(websocket, dependencies)
    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
                await connection.send_error(None, None, "INVALID_COMMAND", "invalid JSON command")
                continue
            command_id, session_id = _command_identity(raw)
            try:
                command = _COMMAND_ADAPTER.validate_python(raw)
            except ValidationError:
                await connection.send_error(
                    command_id,
                    session_id,
                    "INVALID_COMMAND",
                    "command does not match the strict protocol schema",
                )
                continue
            await connection.dispatch(command)
    except WebSocketDisconnect:
        pass
    finally:
        await connection.close()


async def _accept_then_close(websocket: WebSocket, code: int, reason: str) -> None:
    protocols = websocket.scope.get("subprotocols", [])
    selected = WS_SUBPROTOCOL if WS_SUBPROTOCOL in protocols else None
    await websocket.accept(subprotocol=selected)
    await websocket.close(code=code, reason=reason)


class _SessionConnection:
    def __init__(self, websocket: WebSocket, dependencies: ApiDependencies) -> None:
        self._websocket = websocket
        self._dependencies = dependencies
        self._send_lock = asyncio.Lock()
        self._subscription: EventSubscription | None = None
        self._sender: asyncio.Task[None] | None = None

    async def dispatch(self, command: ClientCommand) -> None:
        try:
            if isinstance(command, SessionSubscribeCommand):
                await self._subscribe(command)
                return
            if self._subscription is None or self._subscription.session_id != command.session_id:
                raise StoreError(
                    "SESSION_NOT_SUBSCRIBED",
                    "subscribe to the command session before changing state",
                )
            snapshot = self._dependencies.store.load_snapshot(command.session_id)
            if snapshot.session.requires_recovery_ack and not isinstance(
                command, SessionAckRecoveryCommand
            ):
                raise StoreError(
                    "RECOVERY_ACK_REQUIRED",
                    "only subscription and recovery acknowledgement are allowed",
                )
            coordinator = self._dependencies.coordinator
            if coordinator is None:
                raise StoreError("SERVICE_UNAVAILABLE", "run coordinator is not configured")
            resource_id: str
            if isinstance(command, RunStartCommand):
                run = await coordinator.start_run(
                    command.session_id,
                    command.payload.content,
                    command.client_command_id,
                )
                resource_id = run.id
            elif isinstance(command, RunStopCommand):
                self._require_run_session(command.payload.run_id, command.session_id)
                run = await coordinator.stop_run(
                    command.payload.run_id,
                    command.client_command_id,
                )
                resource_id = run.id
            elif isinstance(command, ApprovalResolveCommand):
                self._require_run_session(command.payload.run_id, command.session_id)
                await coordinator.resolve_approval(
                    command.payload.run_id,
                    command.payload.tool_call_id,
                    command.payload.decision,
                    command.client_command_id,
                )
                resource_id = command.payload.tool_call_id
            elif isinstance(command, SessionAckRecoveryCommand):
                session = await coordinator.acknowledge_recovery(
                    command.session_id,
                    command.client_command_id,
                )
                resource_id = session.id
            elif isinstance(command, SessionCompactCommand):
                session = await coordinator.compact_session(
                    command.session_id,
                    command.client_command_id,
                )
                resource_id = session.id
            elif isinstance(command, SessionClearCommand):
                session = await coordinator.clear_session(
                    command.session_id,
                    command.client_command_id,
                )
                resource_id = session.id
            elif isinstance(command, SessionSetApprovalModeCommand):
                session = await coordinator.set_approval_mode(
                    command.session_id,
                    command.payload.auto_approve,
                    command.client_command_id,
                )
                resource_id = session.id
            else:  # pragma: no cover - exhaustive over the validated command union.
                raise AssertionError(f"unsupported command {type(command).__name__}")
            await self._send(
                AckEnvelope(
                    client_command_id=command.client_command_id,
                    session_id=command.session_id,
                    command_type=command.type,
                    resource_id=resource_id,
                )
            )
        except StoreError as error:
            await self.send_error(
                command.client_command_id,
                command.session_id,
                error.code,
                str(error),
            )
        except sqlite3.Error:
            await self.send_error(
                command.client_command_id,
                command.session_id,
                "INTERNAL_ERROR",
                "durable command transaction failed",
            )

    async def _subscribe(self, command: SessionSubscribeCommand) -> None:
        await self._drop_subscription()
        try:
            async with self._dependencies.event_publisher.session_guard(command.session_id):
                snapshot = self._dependencies.store.load_snapshot(command.session_id)
                subscription = self._dependencies.event_publisher.subscribe_locked(
                    command.session_id,
                    after_seq=snapshot.snapshot_seq,
                )
        except StoreError as error:
            await self.send_error(
                command.client_command_id,
                command.session_id,
                error.code,
                str(error),
            )
            return
        self._subscription = subscription
        await self._send(
            SnapshotEnvelope(
                client_command_id=command.client_command_id,
                session_id=command.session_id,
                snapshot=SessionSnapshotDto.from_domain(snapshot),
            )
        )
        self._sender = asyncio.create_task(
            self._forward(subscription),
            name=f"coding-agent-websocket-{command.session_id}",
        )

    def _require_run_session(self, run_id: str, session_id: str) -> None:
        run = self._dependencies.store.get_run(run_id)
        if run.session_id != session_id:
            raise StoreError("RUN_SESSION_MISMATCH", "run does not belong to command session")

    async def _forward(self, subscription: EventSubscription) -> None:
        try:
            while True:
                message = await subscription.receive()
                if isinstance(message, SubscriptionOverflow):
                    async with self._send_lock:
                        await self._websocket.close(
                            code=WS_CLOSE_SUBSCRIBER_OVERFLOW,
                            reason="subscriber queue overflow; reconnect for a fresh snapshot",
                        )
                    return
                if isinstance(message, DurableEvent):
                    envelope: StrictDto = DurableEnvelope(
                        event=DurableEventDto.from_domain(message)
                    )
                elif isinstance(message, AssistantDelta):
                    envelope = AssistantDeltaEnvelope.from_domain(message)
                elif isinstance(message, AssistantThinkingDelta):
                    envelope = AssistantThinkingDeltaEnvelope.from_domain(message)
                elif isinstance(message, AssistantThinkingClosed):
                    envelope = AssistantThinkingClosedEnvelope.from_domain(message)
                elif isinstance(message, ToolOutputDelta):
                    envelope = ToolOutputDeltaEnvelope.from_domain(message)
                else:  # pragma: no cover - publisher exposes a closed union.
                    raise TypeError(f"unsupported published message: {type(message).__name__}")
                await self._send(envelope)
        except (WebSocketDisconnect, RuntimeError):
            return

    async def send_error(
        self,
        client_command_id: str | None,
        session_id: str | None,
        code: str,
        message: str,
    ) -> None:
        await self._send(
            CommandErrorEnvelope(
                client_command_id=client_command_id,
                session_id=session_id,
                code=code,
                message=message,
            )
        )

    async def _send(self, message: StrictDto) -> None:
        async with self._send_lock:
            await self._websocket.send_json(message.model_dump(mode="json"))

    async def _drop_subscription(self) -> None:
        sender, subscription = self._sender, self._subscription
        self._sender = None
        self._subscription = None
        if sender is not None:
            sender.cancel()
            with suppress(asyncio.CancelledError):
                await sender
        if subscription is not None:
            await self._dependencies.event_publisher.unsubscribe(subscription)

    async def close(self) -> None:
        await self._drop_subscription()


def _command_identity(raw: Any) -> tuple[str | None, str | None]:
    if not isinstance(raw, dict):
        return None, None
    command_id = raw.get("client_command_id")
    session_id = raw.get("session_id")
    return (
        command_id if isinstance(command_id, str) else None,
        session_id if isinstance(session_id, str) else None,
    )


__all__ = [
    "WS_CLOSE_AUTH_EXPIRED",
    "WS_CLOSE_FORBIDDEN",
    "WS_CLOSE_SUBSCRIBER_OVERFLOW",
    "WS_SUBPROTOCOL",
    "router",
]
