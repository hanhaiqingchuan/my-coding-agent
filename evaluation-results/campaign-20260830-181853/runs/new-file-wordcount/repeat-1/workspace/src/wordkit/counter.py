"""Word-frequency helpers."""

from collections import Counter


def word_counts(text: str) -> dict[str, int]:
    """Count words in *text*, case-insensitively.

    Words are separated by runs of whitespace. For example::

        word_counts("Tea tea TIME") == {"tea": 2, "time": 1}
        word_counts("  ") == {}
    """
    return Counter(word.lower() for word in text.split() if word)