"""Step execution for the intake pipeline."""


def run_step(step: str) -> str:
    """Run one step after cleaning its name with the canonical rule."""
    cleaned = step.strip().lower()
    return f"ran {cleaned}"
