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


class StoreError(RuntimeError):
    """A stable persistence-boundary failure suitable for transport mapping."""

    code: str

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CommandIdConflict(StoreError):
    """The same client command id was reused for a different canonical payload."""

    def __init__(self) -> None:
        super().__init__("COMMAND_ID_CONFLICT", "client command id has a different payload")
