from __future__ import annotations

import asyncio

import pytest

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.errors import CancellationRequested
from coding_agent.core.models import ApprovalDecision, PreparedToolCall, ToolCall
from coding_agent.runtime.approval import ApprovalGate


def _write_call(call_id: str = "call-write") -> PreparedToolCall:
    return PreparedToolCall(
        call=ToolCall(call_id, "write_file", {"operation": "write", "path": "a.txt"}),
        requires_approval=True,
        target="/workspace/a.txt",
        preview="diff",
    )


@pytest.mark.asyncio
async def test_read_only_call_is_approved_without_entering_pending_queue() -> None:
    """Gating a read would stall an operation that the policy explicitly auto-approves."""
    gate = ApprovalGate()
    prepared = PreparedToolCall(
        call=ToolCall("call-read", "read_file", {"path": "a.txt"}),
        requires_approval=False,
    )

    decision = await gate.request(prepared, CancellationToken())

    assert decision is ApprovalDecision.APPROVE
    assert gate.pending == ()


@pytest.mark.asyncio
async def test_interactive_decision_resolves_the_frozen_prepared_call() -> None:
    """Resolving by mutable parameters instead of call id could approve a different effect."""
    gate = ApprovalGate()
    prepared = _write_call()
    waiting = asyncio.create_task(gate.request(prepared, CancellationToken()))

    requested = await gate.next_request()
    gate.resolve(requested.call.id, ApprovalDecision.REJECT)

    assert requested is prepared
    assert await waiting is ApprovalDecision.REJECT
    assert gate.pending == ()


@pytest.mark.asyncio
async def test_cancellation_wakes_an_interactive_approval_waiter() -> None:
    """Ignoring cancellation would leave a stopped run stuck awaiting user input forever."""
    gate = ApprovalGate()
    cancellation = CancellationToken()
    waiting = asyncio.create_task(gate.request(_write_call(), cancellation))
    await gate.next_request()

    cancellation.cancel()

    with pytest.raises(CancellationRequested):
        await waiting
    assert gate.pending == ()


@pytest.mark.asyncio
async def test_cancelled_request_is_not_delivered_later_as_stale_approval() -> None:
    """Leaving a cancelled item queued could approve a call belonging to an ended run."""
    gate = ApprovalGate()
    cancellation = CancellationToken()
    waiting = asyncio.create_task(gate.request(_write_call(), cancellation))
    while not gate.pending:
        await asyncio.sleep(0)

    cancellation.cancel()
    with pytest.raises(CancellationRequested):
        await waiting

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(gate.next_request(), 0.02)


@pytest.mark.asyncio
async def test_trusted_mode_auto_approves_but_still_exposes_the_policy_result() -> None:
    """Trusted mode returning no decision would prevent the loop from auditing auto-approval."""
    gate = ApprovalGate(auto_approve=True)

    decision = await gate.request(_write_call(), CancellationToken())

    assert decision is ApprovalDecision.APPROVE
    assert gate.pending == ()


@pytest.mark.asyncio
async def test_session_auto_approve_mode_approves_without_entering_the_pending_queue() -> None:
    """The per-session toggle (spec 13.4) must behave exactly like trusted mode."""
    gate = ApprovalGate()

    decision = await gate.request(_write_call(), CancellationToken(), session_auto_approve=True)

    assert decision is ApprovalDecision.APPROVE
    assert gate.pending == ()


@pytest.mark.asyncio
async def test_session_mode_false_never_downgrades_a_trusted_process() -> None:
    """A session toggle to manual must not silently revoke a --yes process's trust."""
    gate = ApprovalGate(auto_approve=True)

    decision = await gate.request(_write_call(), CancellationToken(), session_auto_approve=False)

    assert decision is ApprovalDecision.APPROVE
    assert gate.pending == ()


@pytest.mark.asyncio
async def test_session_mode_false_keeps_the_gate_interactive() -> None:
    """The default session mode still queues the call for a human decision."""
    gate = ApprovalGate()

    waiting = asyncio.create_task(
        gate.request(_write_call(), CancellationToken(), session_auto_approve=False)
    )
    requested = await gate.next_request()

    assert gate.pending == (requested,)
    gate.resolve(requested.call.id, ApprovalDecision.REJECT)
    assert await waiting is ApprovalDecision.REJECT
