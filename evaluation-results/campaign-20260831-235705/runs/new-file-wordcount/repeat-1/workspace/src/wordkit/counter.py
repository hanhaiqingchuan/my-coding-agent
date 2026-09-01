"""Word-frequency helpers."""


def word_counts(text: str) -> dict[str, int]:
    """Count whitespace-separated words in ``text``, case-insensitively."""
    counts: dict[str, int] = {}
    for word in text.split():
        key = word.lower()
        counts[key] = counts.get(key, 0) + 1
    return counts