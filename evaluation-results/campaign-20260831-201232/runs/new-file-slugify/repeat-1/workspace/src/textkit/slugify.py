"""URL slug helpers for textkit."""


def slugify(text: str) -> str:
    """Return a URL-safe slug for *text*.

    The result is lowercase, contains only ASCII letters and digits
    separated by single hyphens, and is ``""`` when *text* has no
    letters or digits.
    """
    words: list[str] = []
    current: list[str] = []

    for char in text.lower():
        if ("a" <= char <= "z") or ("0" <= char <= "9"):
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []

    if current:
        words.append("".join(current))

    return "-".join(words)