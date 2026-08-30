"""Case-sensitive word frequency counting."""


def word_counts(text: str) -> dict[str, int]:
    """Count words without folding case, so ``Tea`` and ``tea`` stay distinct."""
    counts: dict[str, int] = {}
    for word in text.split():
        counts[word] = counts.get(word, 0) + 1
    return counts
