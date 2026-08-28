"""Linearize run cancellation with the start of local tool effects."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from typing import Protocol

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.events import RunOutcome
from coding_agent.core.models import (
    ApprovalDecision,
    ApprovalRecord,
    AssistantTurn,
    EffectStartResult,
    Run,
    RunState,
    Session,
)
from coding_agent.runtime.publisher import EventPublisher
from coding_agent.storage.sqlite import SQLiteStore

_LOGGER = logging.getLogger(__name__)


class RunExecutor(Protocol):
    """The narrow AgentLoop surface owned by the coordinator."""

    async def run(
        self,
        run_id: str,
        session_id: str,
        cancellation: CancellationToken,
    ) -> RunOutcome: ...


class ApprovalResolver(Protocol):
    def resolve_persisted(self, tool_call_id: str, decision: ApprovalDecision) -> None: ...


class RunMutationGate:
    """Share one process-local critical section for Stop and effect-start mutations."""

    def __init__(self, store: SQLiteStore, publisher: EventPublisher) -> None:
        self._store = store
        self._publisher = publisher
        self._lock = asyncio.Lock()
        self._cancellations: dict[str, CancellationToken] = {}

    @property
    def event_publisher(self) -> EventPublisher:
        return self._publisher

    async def register_cancellation(self, run_id: str, cancellation: CancellationToken) -> Run:
        """Register a token and observe persisted Stop under the mutation lock."""
        async with self._lock:
            run = self._store.get_run(run_id)
            self._register_committed_cancellation(run, cancellation)
            return run

    def _register_committed_cancellation(
        self,
        run: Run,
        cancellation: CancellationToken,
    ) -> None:
        """Attach a token synchronously while the mutation lock still guards the commit."""
        self._cancellations[run.id] = cancellation
        if run.state is RunState.CANCELLING or run.cancellation_requested_at is not None:
            cancellation.cancel()

    async def unregister_cancellation(self, run_id: str) -> None:
        async with self._lock:
            self._cancellations.pop(run_id, None)

    async def begin_run(
        self,
        session_id: str,
        content: str,
        config_snapshot: Mapping[str, object],
        client_command_id: str,
        on_committed: Callable[[Run], None] | None = None,
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
            if on_committed is not None:
                on_committed(run)
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
        on_committed: Callable[[ApprovalRecord], None] | None = None,
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
            if on_committed is not None:
                on_committed(approval)
            await self._publish_after(current.session_id, previous_seq)
            return approval

    async def acknowledge_recovery(
        self,
        session_id: str,
        client_command_id: str,
    ) -> Session:
        async with self._lock:
            previous_seq = _latest_seq(self._store, session_id)
            digest = hashlib.sha256(f"session.ack_recovery\0{session_id}".encode()).hexdigest()
            session = self._store.acknowledge_recovery(
                session_id,
                client_command_id,
                digest,
            )
            await self._publish_after(session_id, previous_seq)
            return session

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
            try:
                await self._publisher.publish_committed(event)
            except Exception:
                _LOGGER.exception(
                    "durable event broadcast failed",
                    extra={"session_id": session_id, "event_seq": event.seq},
                )
                return


class RunCoordinator:
    """Own each process-local run task and its cancellation token exactly once."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        mutation_gate: RunMutationGate,
        runner: RunExecutor,
        config_snapshot: Mapping[str, object],
        approval_gate: ApprovalResolver | None = None,
    ) -> None:
        self._store = store
        self._mutation_gate = mutation_gate
        self._runner = runner
        self._config_snapshot = dict(config_snapshot)
        self._approval_gate = approval_gate
        self._ownership_lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[RunOutcome]] = {}

    @property
    def event_publisher(self) -> EventPublisher:
        return self._mutation_gate.event_publisher

    async def start_run(
        self,
        session_id: str,
        content: str,
        client_command_id: str,
    ) -> Run:
        """Claim the global run slot and start one background owner for a new run."""
        async with self._ownership_lock:

            def own_committed_run(run: Run) -> None:
                if run.id in self._tasks or run.finished_at is not None:
                    return
                cancellation = CancellationToken()
                self._mutation_gate._register_committed_cancellation(run, cancellation)
                self._tasks[run.id] = asyncio.create_task(
                    self._runner.run(run.id, run.session_id, cancellation),
                    name=f"coding-agent-run-{run.id}",
                )

            run = await self._mutation_gate.begin_run(
                session_id,
                content,
                self._config_snapshot,
                client_command_id,
                on_committed=own_committed_run,
            )
            return run

    async def stop_run(self, run_id: str, client_command_id: str) -> Run:
        """Persist Stop before the mutation gate signals the registered token."""
        return await self._mutation_gate.request_stop(run_id, client_command_id)

    async def wait_for_run(self, run_id: str) -> Run:
        """Wait for this process-owned run, then return its persisted terminal record."""
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)
        return self._store.get_run(run_id)

    async def resolve_approval(
        self,
        run_id: str,
        tool_call_id: str,
        decision: ApprovalDecision,
        client_command_id: str,
    ) -> None:
        """Persist an approval command before waking the in-memory loop waiter."""

        def deliver_committed_approval(_: ApprovalRecord) -> None:
            if self._approval_gate is not None:
                self._approval_gate.resolve_persisted(tool_call_id, decision)

        await self._mutation_gate.resolve_approval(
            run_id,
            tool_call_id,
            decision,
            client_command_id,
            on_committed=deliver_committed_approval,
        )

    async def acknowledge_recovery(
        self,
        session_id: str,
        client_command_id: str,
    ) -> Session:
        return await self._mutation_gate.acknowledge_recovery(session_id, client_command_id)


def _latest_seq(store: SQLiteStore, session_id: str) -> int:
    events = store.events_after(session_id, 0)
    return events[-1].seq if events else 0


__all__ = ["ApprovalResolver", "RunCoordinator", "RunExecutor", "RunMutationGate"]
