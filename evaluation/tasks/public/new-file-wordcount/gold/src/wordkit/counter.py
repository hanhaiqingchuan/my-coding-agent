"""Case-insensitive word frequency counting."""


def word_counts(text: str) -> dict[str, int]:
    """Return how often each whitespace-separated word occurs, lowercased."""
    counts: dict[str, int] = {}
    for word in text.lower().split():
        counts[word] = counts.get(word, 0) + 1
    return counts
