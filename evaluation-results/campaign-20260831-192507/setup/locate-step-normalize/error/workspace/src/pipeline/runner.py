"""Step execution for the intake pipeline."""

from pipeline.steps import normalize_step


def run_step(step: str) -> str:
    """Run one step after cleaning its name with the canonical rule."""
    return f"ran {normalize_step(step)}"
