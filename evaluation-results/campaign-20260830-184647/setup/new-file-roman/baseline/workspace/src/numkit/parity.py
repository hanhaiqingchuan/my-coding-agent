"""Parity helpers."""


def is_even(value: int) -> bool:
    """Return whether ``value`` is divisible by two."""
    return value % 2 == 0


def is_odd(value: int) -> bool:
    """Return whether ``value`` is not divisible by two."""
    return value % 2 == 1
