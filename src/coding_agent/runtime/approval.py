"""Cancellation-aware approval decisions over immutable prepared tool calls."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.errors import CancellationRequested
from coding_agent.core.models import ApprovalDecision, PreparedToolCall


class ApprovalGate:
    """Hold interactive approvals without allowing a client to replace frozen arguments."""

    def __init__(self, *, auto_approve: bool = False) -> None:
        self.auto_approve = auto_approve
        self._requests: asyncio.Queue[PreparedToolCall] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future[ApprovalDecision]] = {}
        self._prepared: dict[str, PreparedToolCall] = {}
        self._early_decisions: dict[str, ApprovalDecision] = {}
        self._delivered_decisions: dict[str, ApprovalDecision] = {}

    @property
    def pending(self) -> tuple[PreparedToolCall, ...]:
        return tuple(self._prepared.values())

    async def next_request(self) -> PreparedToolCall:
        """Return the next immutable proposal for an interactive delivery layer."""
        while True:
            prepared = await self._requests.get()
            if prepared.call.id in self._pending:
                return prepared

    async def request(
        self,
        prepared: PreparedToolCall,
        cancellation: CancellationToken,
    ) -> ApprovalDecision:
        if not prepared.requires_approval or self.auto_approve:
            cancellation.raise_if_cancelled()
            return ApprovalDecision.APPROVE

        cancellation.raise_if_cancelled()
        call_id = prepared.call.id
        early = self._early_decisions.pop(call_id, None)
        if early is not None:
            return early
        if call_id in self._pending:
            raise ValueError(f"approval already pending: {call_id}")
        loop = asyncio.get_running_loop()
        decision = loop.create_future()
        self._pending[call_id] = decision
        self._prepared[call_id] = prepared
        await self._requests.put(prepared)
        cancellation_wait = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {decision, cancellation_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancellation_wait in done:
                raise CancellationRequested()
            return decision.result()
        finally:
            self._pending.pop(call_id, None)
            self._prepared.pop(call_id, None)
            if not decision.done():
                decision.cancel()
            cancellation_wait.cancel()
            with suppress(asyncio.CancelledError):
                await cancellation_wait

    def resolve(self, tool_call_id: str, decision: ApprovalDecision) -> None:
        """Resolve exactly the pending backend call id; arguments are never accepted here."""
        pending = self._pending.get(tool_call_id)
        if pending is None or pending.done():
            raise KeyError(f"approval is not pending: {tool_call_id}")
        pending.set_result(decision)

    def resolve_persisted(self, tool_call_id: str, decision: ApprovalDecision) -> None:
        """Deliver an already-audited decision, retaining it if the waiter is not ready yet."""
        delivered = self._delivered_decisions.get(tool_call_id)
        if delivered is not None:
            if delivered is not decision:
                raise ValueError("persisted approval decision changed")
            return
        self._delivered_decisions[tool_call_id] = decision
        pending = self._pending.get(tool_call_id)
        if pending is None:
            self._early_decisions[tool_call_id] = decision
        elif not pending.done():
            pending.set_result(decision)

    def is_persisted(self, tool_call_id: str) -> bool:
        """Whether the delivery layer already wrote this decision to durable storage."""
        return tool_call_id in self._delivered_decisions


__all__ = ["ApprovalGate"]
