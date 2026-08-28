"""Linearize run cancellation with the start of local tool effects."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.events import RunOutcome
from coding_agent.core.models import (
    ApprovalDecision,
    ApprovalRecord,
    AssistantTurn,
    EffectStartResult,
    Run,
    RunState,
)
from coding_agent.runtime.publisher import EventPublisher
from coding_agent.storage.sqlite import SQLiteStore


class RunMutationGate:
    """Share one process-local critical section for Stop and effect-start mutations."""

    def __init__(self, store: SQLiteStore, publisher: EventPublisher) -> None:
        self._store = store
        self._publisher = publisher
        self._lock = asyncio.Lock()
        self._cancellations: dict[str, CancellationToken] = {}

    async def register_cancellation(self, run_id: str, cancellation: CancellationToken) -> Run:
        """Register a token and observe persisted Stop under the mutation lock."""
        async with self._lock:
            run = self._store.get_run(run_id)
            self._cancellations[run_id] = cancellation
            if run.state is RunState.CANCELLING or run.cancellation_requested_at is not None:
                cancellation.cancel()
            return run

    async def unregister_cancellation(self, run_id: str) -> None:
        async with self._lock:
            self._cancellations.pop(run_id, None)

    async def begin_run(
        self,
        session_id: str,
        content: str,
        config_snapshot: Mapping[str, object],
        client_command_id: str,
    ) -> Run:
        """Serialize the process-wide active-run claim and publish only after commit."""
        async with self._lock:
            previous_seq = _latest_seq(self._store, session_id)
            payload = json.dumps(
                {"content": content, "config": config_snapshot},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            digest = hashlib.sha256(payload.encode()).hexdigest()
            run = self._store.begin_run(
                session_id, content, config_snapshot, client_command_id, digest
            )
            await self._publish_after(session_id, previous_seq)
            return run

    async def begin_effect(self, run_id: str, tool_call_id: str) -> EffectStartResult:
        async with self._lock:
            run = self._store.get_run(run_id)
            previous_seq = _latest_seq(self._store, run.session_id)
            result = self._store.begin_effect(run_id, tool_call_id)
            await self._publish_after(run.session_id, previous_seq)
            return result

    async def request_stop(self, run_id: str, client_command_id: str) -> Run:
        async with self._lock:
            current = self._store.get_run(run_id)
            previous_seq = _latest_seq(self._store, current.session_id)
            digest = hashlib.sha256(f"run.stop\0{run_id}".encode()).hexdigest()
            run = self._store.request_cancellation(run_id, client_command_id, digest)
            cancellation = self._cancellations.get(run_id)
            if cancellation is not None:
                cancellation.cancel()
            await self._publish_after(run.session_id, previous_seq)
            return run

    async def resolve_approval(
        self,
        run_id: str,
        tool_call_id: str,
        decision: ApprovalDecision,
        client_command_id: str,
    ) -> ApprovalRecord:
        async with self._lock:
            current = self._store.get_run(run_id)
            previous_seq = _latest_seq(self._store, current.session_id)
            digest = hashlib.sha256(
                f"approval.resolve\0{run_id}\0{tool_call_id}\0{decision.value}".encode()
            ).hexdigest()
            approval = self._store.resolve_approval(
                run_id,
                tool_call_id,
                decision,
                client_command_id,
                digest,
            )
            await self._publish_after(current.session_id, previous_seq)
            return approval

    async def finish_run(self, run_id: str, outcome: RunOutcome) -> RunOutcome:
        """Let a persisted Stop win over any late non-tool terminal outcome."""
        async with self._lock:
            current = self._store.get_run(run_id)
            previous_seq = _latest_seq(self._store, current.session_id)
            actual = RunOutcome.cancel() if current.state is RunState.CANCELLING else outcome
            self._store.transition_run(
                run_id,
                {current.state},
                actual.state,
                actual.stop_reason,
                actual.error_kind,
            )
            await self._publish_after(current.session_id, previous_seq)
            return actual

    async def commit_final_turn(
        self, run_id: str, turn: AssistantTurn, outcome: RunOutcome
    ) -> RunOutcome:
        """Keep final transcript commit and terminal selection ordered against Stop."""
        async with self._lock:
            current = self._store.get_run(run_id)
            previous_seq = _latest_seq(self._store, current.session_id)
            if current.state is RunState.CANCELLING:
                actual = RunOutcome.cancel()
                self._store.transition_run(
                    run_id,
                    {current.state},
                    actual.state,
                    actual.stop_reason,
                    actual.error_kind,
                )
            else:
                actual = outcome
                self._store.commit_final_turn_and_finish(
                    run_id,
                    turn,
                    state=actual.state,
                    stop_reason=actual.stop_reason,
                    error_kind=actual.error_kind,
                )
            await self._publish_after(current.session_id, previous_seq)
            return actual

    async def _publish_after(self, session_id: str, previous_seq: int) -> None:
        for event in self._store.events_after(session_id, previous_seq):
            await self._publisher.publish_committed(event)


def _latest_seq(store: SQLiteStore, session_id: str) -> int:
    events = store.events_after(session_id, 0)
    return events[-1].seq if events else 0


__all__ = ["RunMutationGate"]
