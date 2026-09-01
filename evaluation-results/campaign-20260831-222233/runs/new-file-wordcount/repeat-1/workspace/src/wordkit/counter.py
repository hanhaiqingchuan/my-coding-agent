"""Word-frequency helpers."""

from collections import Counter


def word_counts(text: str) -> dict[str, int]:
    """Count words in *text* by runs of whitespace, case-insensitively."""
    return dict(Counter(word.lower() for word in text.split()))