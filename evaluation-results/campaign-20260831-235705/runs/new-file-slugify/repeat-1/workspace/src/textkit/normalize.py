"""Whitespace normalization helpers."""


def squeeze_spaces(text: str) -> str:
    """Collapse every run of whitespace into one space and strip the result."""
    return " ".join(text.split())
