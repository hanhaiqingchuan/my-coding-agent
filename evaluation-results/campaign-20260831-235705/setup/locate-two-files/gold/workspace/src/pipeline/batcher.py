"""Batch splitting for the ingest pipeline."""

from pipeline.settings import MAX_BATCH_SIZE


def batches(items: list[int]) -> list[list[int]]:
    """Split ``items`` into consecutive batches of at most ``MAX_BATCH_SIZE`` entries."""
    limit = MAX_BATCH_SIZE
    return [list(items[start : start + limit]) for start in range(0, len(items), limit)]
