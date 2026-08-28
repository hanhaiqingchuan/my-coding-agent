from __future__ import annotations

import pytest

from coding_agent.core.errors import InvalidStateTransition
from coding_agent.core.events import RunOutcome, terminal_state_for, validate_transition
from coding_agent.core.models import RunState, StopReason


def test_terminal_state_cannot_transition() -> None:
    """Letting a completed run stream again would let late work overwrite its durable end."""
    with pytest.raises(InvalidStateTransition):
        validate_transition(RunState.COMPLETED, RunState.MODEL_STREAMING)


@pytest.mark.parametrize(
    ("reason", "state"),
    [
        (StopReason.COMPLETED, RunState.COMPLETED),
        (StopReason.USER_STOP, RunState.CANCELLED),
        (StopReason.MAX_ROUNDS, RunState.STOPPED),
        (StopReason.MODEL_REFUSAL, RunState.STOPPED),
        (StopReason.PAUSE_TURN, RunState.STOPPED),
        (StopReason.SERVER_RESTART, RunState.INTERRUPTED),
        (StopReason.RETRY_EXHAUSTED, RunState.FAILED),
    ],
)
def test_stop_reason_maps_to_terminal_state(reason: StopReason, state: RunState) -> None:
    """Changing this mapping would report a structured stop reason as the wrong lifecycle end."""
    assert terminal_state_for(reason) is state


def test_every_active_state_can_enter_cancelling() -> None:
    """Omitting any active state would make Stop unreliable during that phase."""
    active_states = (
        RunState.STARTING,
        RunState.BUILDING_CONTEXT,
        RunState.COMPACTING,
        RunState.MODEL_STREAMING,
        RunState.RETRY_WAIT,
        RunState.AWAITING_APPROVAL,
        RunState.TOOL_RUNNING,
    )

    for state in active_states:
        validate_transition(state, RunState.CANCELLING)


def test_tool_phase_edges_the_product_takes_are_modeled() -> None:
    """An auto-executed read and a mixed group are real paths; unmodeled edges write silently."""
    validate_transition(RunState.MODEL_STREAMING, RunState.TOOL_RUNNING)
    validate_transition(RunState.TOOL_RUNNING, RunState.AWAITING_APPROVAL)

    with pytest.raises(InvalidStateTransition):
        validate_transition(RunState.TOOL_RUNNING, RunState.MODEL_STREAMING)
    with pytest.raises(InvalidStateTransition):
        validate_transition(RunState.AWAITING_APPROVAL, RunState.MODEL_STREAMING)


def test_cancelling_only_reaches_cancelled() -> None:
    """Allowing cancellation to resume work would violate the persisted Stop linearization point."""
    validate_transition(RunState.CANCELLING, RunState.CANCELLED)

    with pytest.raises(InvalidStateTransition):
        validate_transition(RunState.CANCELLING, RunState.MODEL_STREAMING)


def test_run_outcome_uses_structured_reason_not_human_text() -> None:
    """Replacing the enum mapping with prose would make terminal-state selection ambiguous."""
    outcome = RunOutcome.stop(StopReason.DOOM_LOOP)

    assert outcome.state is RunState.STOPPED
    assert outcome.stop_reason is StopReason.DOOM_LOOP
