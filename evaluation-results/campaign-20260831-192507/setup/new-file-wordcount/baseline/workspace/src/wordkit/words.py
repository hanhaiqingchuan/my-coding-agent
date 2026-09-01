"""Whitespace tokenizing helpers."""


def split_words(text: str) -> list[str]:
    """Split ``text`` on runs of whitespace, dropping empty pieces."""
    return text.split()
