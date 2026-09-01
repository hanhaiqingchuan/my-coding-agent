"""Step-name normalization rules."""


def normalize_step(step: str) -> str:
    """Return the canonical form of a step name: trimmed, slash-trimmed, and lowercased."""
    return step.strip().strip("/").strip().lower()