"""URL slug generation helpers."""

import re

_NON_ASCII_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


def slugify(text: str) -> str:
    """Turn *text* into an ASCII URL slug.

    The text is lowercased, runs of characters other than ASCII letters or
    digits are collapsed into a single ``-``, and leading/trailing dashes are
    removed. Text without any letters or digits yields an empty string.
    """
    return _NON_ASCII_ALNUM_RE.sub("-", text.lower()).strip("-")