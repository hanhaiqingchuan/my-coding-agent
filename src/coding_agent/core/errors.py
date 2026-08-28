"""Structured domain exceptions that do not depend on delivery infrastructure."""

from __future__ import annotations


class InvalidStateTransition(ValueError):
    """Raised when a run lifecycle update is not in the explicit transition table."""

    def __init__(self, current: object, target: object) -> None:
        super().__init__(f"invalid run state transition: {current!s} -> {target!s}")
        self.current = current
        self.target = target


class CancellationRequested(RuntimeError):
    """Raised at a cooperative cancellation checkpoint."""
