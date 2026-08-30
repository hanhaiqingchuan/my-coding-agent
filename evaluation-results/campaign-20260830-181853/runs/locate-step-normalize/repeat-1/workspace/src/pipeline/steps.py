"""Step-name normalization rules."""


def normalize_step(step: str) -> str:
    """Return the canonical form of a step name: trimmed, lowercased, and
    with leading and trailing slash characters removed."""
    return step.strip().strip("/").strip().lower()