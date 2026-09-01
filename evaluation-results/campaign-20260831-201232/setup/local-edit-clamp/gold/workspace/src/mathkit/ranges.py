"""Numeric range helpers."""


def clamp(value: int, lower: int, upper: int) -> int:
    """Return ``value`` limited to the inclusive range ``[lower, upper]``."""
    if lower > upper:
        raise ValueError("lower must not exceed upper")
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def midpoint(lower: int, upper: int) -> float:
    """Return the midpoint of the inclusive range ``[lower, upper]``."""
    return (lower + upper) / 2
