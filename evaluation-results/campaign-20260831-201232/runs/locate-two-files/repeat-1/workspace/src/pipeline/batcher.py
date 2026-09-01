"""Batch splitting for the ingest pipeline."""

from pipeline.settings import MAX_BATCH_SIZE


def batches(items: list[int]) -> list[list[int]]:
    """Split ``items`` into consecutive batches of at most ``MAX_BATCH_SIZE`` entries."""
    return [list(items[start : start + MAX_BATCH_SIZE]) for start in range(0, len(items), MAX_BATCH_SIZE)]