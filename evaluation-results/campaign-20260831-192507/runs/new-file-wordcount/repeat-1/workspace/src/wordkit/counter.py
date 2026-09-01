"""Word-frequency helpers."""


def word_counts(text: str) -> dict[str, int]:
    """Count occurrences of each word in ``text``, case-insensitively.

    Words are separated by runs of whitespace. Text with no words yields an
    empty dict.
    """
    counts: dict[str, int] = {}
    for word in text.split():
        word = word.lower()
        counts[word] = counts.get(word, 0) + 1
    return counts