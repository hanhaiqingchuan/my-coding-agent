"""Summary statistics for small samples."""


def mean(values: list[float]) -> float:
    """Return the arithmetic mean, or 0.0 when there is nothing to average."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def median(values: list[float]) -> float:
    """Return the median of ``values``; assumes the list is not empty."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
