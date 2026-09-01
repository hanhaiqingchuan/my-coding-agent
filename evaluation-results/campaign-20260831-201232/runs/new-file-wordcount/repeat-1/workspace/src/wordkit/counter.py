"""Word-frequency helpers."""


def word_counts(text: str) -> dict[str, int]:
    """Count the occurrences of each word in *text*.

    The text is split on runs of whitespace and words are counted
    case-insensitively. Returns an empty dict for text with no words.
    """
    counts: dict[str, int] = {}
    for word in text.split():
        counts[word.lower()] = counts.get(word.lower(), 0) + 1
    return counts