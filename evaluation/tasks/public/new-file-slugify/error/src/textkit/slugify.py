"""ASCII URL slug generation that keeps trailing separators."""


def slugify(text: str) -> str:
    """Return a slug without removing leading or trailing separators."""
    result = []
    for character in text.lower():
        if character.isascii() and character.isalnum():
            result.append(character)
        else:
            result.append("-")
    return "".join(result)
