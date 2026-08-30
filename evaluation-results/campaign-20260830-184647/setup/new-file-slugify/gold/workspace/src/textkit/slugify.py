"""ASCII URL slug generation."""


def slugify(text: str) -> str:
    """Return a lowercase, hyphen-separated ASCII slug for ``text``."""
    pieces: list[str] = []
    current: list[str] = []
    for character in text.lower():
        if character.isascii() and character.isalnum():
            current.append(character)
        elif current:
            pieces.append("".join(current))
            current = []
    if current:
        pieces.append("".join(current))
    return "-".join(pieces)
