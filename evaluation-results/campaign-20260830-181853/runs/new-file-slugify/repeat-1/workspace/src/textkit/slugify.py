"""URL slug helper."""

import re

_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Return a URL-safe slug derived from *text*.

    The text is lowercased, and every run of characters that are not
    ASCII letters or digits is replaced with a single ``-``. Leading
    and trailing ``-`` characters are removed. If the text contains no
    ASCII letters or digits, an empty string is returned.
    """
    return _NON_SLUG_CHARS.sub("-", text.lower()).strip("-")