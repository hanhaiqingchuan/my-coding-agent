def word_counts(text: str) -> dict[str, int]:
    """Count the words in *text* case-insensitively.

    Words are separated by runs of whitespace. Text with no words
    yields an empty dict.
    """
    counts: dict[str, int] = {}
    for word in text.split():
        lowered = word.lower()
        counts[lowered] = counts.get(lowered, 0) + 1
    return counts