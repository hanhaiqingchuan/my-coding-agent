"""Batch splitting for the ingest pipeline."""


def batches(items: list[int]) -> list[list[int]]:
    """Split ``items`` into consecutive batches of at most 50 entries."""
    limit = 50
    return [list(items[start : start + limit]) for start in range(0, len(items), limit)]
