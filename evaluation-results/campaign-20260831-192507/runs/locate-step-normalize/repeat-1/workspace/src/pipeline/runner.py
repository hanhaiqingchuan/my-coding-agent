"""Step execution for the intake pipeline."""

from .steps import normalize_step


def run_step(step: str) -> str:
    """Run one step after cleaning its name with the canonical rule."""
    cleaned = normalize_step(step)
    return f"ran {cleaned}"