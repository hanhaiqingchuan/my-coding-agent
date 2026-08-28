"""Pure run-lifecycle rules, kept independent of storage and transport."""

from __future__ import annotations

from dataclasses import dataclass

from coding_agent.core.errors import InvalidStateTransition
from coding_agent.core.models import ErrorKind, RunState, StopReason

_TERMINAL_STATES = frozenset(
    {
        RunState.COMPLETED,
        RunState.STOPPED,
        RunState.CANCELLED,
        RunState.FAILED,
        RunState.INTERRUPTED,
    }
)
_ACTIVE_STATES = frozenset(set(RunState) - _TERMINAL_STATES - {RunState.CANCELLING})

_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.STARTING: frozenset({RunState.BUILDING_CONTEXT, RunState.CANCELLING}),
    RunState.BUILDING_CONTEXT: frozenset(
        {RunState.COMPACTING, RunState.MODEL_STREAMING, RunState.CANCELLING}
    ),
    RunState.COMPACTING: frozenset({RunState.BUILDING_CONTEXT, RunState.CANCELLING}),
    RunState.MODEL_STREAMING: frozenset(
        {
            RunState.BUILDING_CONTEXT,
            RunState.RETRY_WAIT,
            RunState.AWAITING_APPROVAL,
            RunState.TOOL_RUNNING,
            RunState.COMPLETED,
            RunState.CANCELLING,
        }
    ),
    RunState.RETRY_WAIT: frozenset({RunState.MODEL_STREAMING, RunState.CANCELLING}),
    RunState.AWAITING_APPROVAL: frozenset(
        {RunState.BUILDING_CONTEXT, RunState.TOOL_RUNNING, RunState.CANCELLING}
    ),
    RunState.TOOL_RUNNING: frozenset(
        {RunState.BUILDING_CONTEXT, RunState.AWAITING_APPROVAL, RunState.CANCELLING}
    ),
    RunState.CANCELLING: frozenset({RunState.CANCELLED}),
    RunState.COMPLETED: frozenset(),
    RunState.STOPPED: frozenset(),
    RunState.CANCELLED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.INTERRUPTED: frozenset(),
}

_STOP_TO_TERMINAL = {
    StopReason.COMPLETED: RunState.COMPLETED,
    StopReason.USER_STOP: RunState.CANCELLED,
    StopReason.MAX_ROUNDS: RunState.STOPPED,
    StopReason.DOOM_LOOP: RunState.STOPPED,
    StopReason.EMPTY_RESPONSE: RunState.STOPPED,
    StopReason.OUTPUT_TRUNCATED: RunState.STOPPED,
    StopReason.INCOMPLETE_TOOL_CALL: RunState.STOPPED,
    StopReason.MODEL_REFUSAL: RunState.STOPPED,
    StopReason.PAUSE_TURN: RunState.STOPPED,
    StopReason.SERVER_RESTART: RunState.INTERRUPTED,
    StopReason.MODEL_PROTOCOL_ERROR: RunState.FAILED,
    StopReason.AUTH_ERROR: RunState.FAILED,
    StopReason.CONFIG_ERROR: RunState.FAILED,
    StopReason.RETRY_EXHAUSTED: RunState.FAILED,
    StopReason.CONTEXT_OVERFLOW: RunState.FAILED,
}


def validate_transition(current: RunState, target: RunState) -> None:
    """Require a lifecycle update to be one of the explicitly modeled edges."""
    if target in _TRANSITIONS[current]:
        return
    if current in _ACTIVE_STATES and target in {
        RunState.STOPPED,
        RunState.FAILED,
        RunState.INTERRUPTED,
    }:
        return
    if current is RunState.MODEL_STREAMING and target is RunState.COMPLETED:
        return
    raise InvalidStateTransition(current, target)


def terminal_state_for(reason: StopReason) -> RunState:
    """Map every structured end reason to its single valid terminal state."""
    return _STOP_TO_TERMINAL[reason]


def retry_wait_payload(
    *,
    attempt: int,
    max_attempts: int,
    delay_seconds: float,
    reason: str,
    deadline_monotonic: float,
) -> dict[str, int | float | str]:
    """Build the stable durable payload published before a model retry wait.

    The deadline deliberately uses the retry owner's injected monotonic clock.  A
    coordinator can translate it to wall-clock UI data without making retry policy
    depend on wall-clock jumps.
    """
    if not 1 <= attempt <= max_attempts:
        raise ValueError("retry attempt must be within max attempts")
    if delay_seconds < 0:
        raise ValueError("retry delay must not be negative")
    if not reason:
        raise ValueError("retry reason must not be empty")
    return {
        "attempt": attempt,
        "max_attempts": max_attempts,
        "delay_seconds": delay_seconds,
        "reason": reason,
        "deadline_monotonic": deadline_monotonic,
    }


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """A structured agent-loop outcome; it never uses natural-language error text as policy."""

    state: RunState
    stop_reason: StopReason
    error_kind: ErrorKind | None = None

    def __post_init__(self) -> None:
        if terminal_state_for(self.stop_reason) is not self.state:
            raise ValueError("run outcome state must match its stop reason")

    @classmethod
    def complete(cls) -> RunOutcome:
        return cls(RunState.COMPLETED, StopReason.COMPLETED)

    @classmethod
    def stop(cls, reason: StopReason) -> RunOutcome:
        if terminal_state_for(reason) is not RunState.STOPPED:
            raise ValueError("stop outcomes require a stopped reason")
        return cls(RunState.STOPPED, reason)

    @classmethod
    def cancel(cls) -> RunOutcome:
        return cls(RunState.CANCELLED, StopReason.USER_STOP)

    @classmethod
    def fail(cls, reason: StopReason, error_kind: ErrorKind) -> RunOutcome:
        if terminal_state_for(reason) is not RunState.FAILED:
            raise ValueError("failed outcomes require a failed reason")
        return cls(RunState.FAILED, reason, error_kind)
