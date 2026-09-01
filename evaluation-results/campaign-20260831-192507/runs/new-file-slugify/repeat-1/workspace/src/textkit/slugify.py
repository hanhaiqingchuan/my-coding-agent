"""URL slug helper for :mod:`textkit`."""

import re


_NON_ASCII_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def slugify(text: str) -> str:
    """Return *text* as a lowercase URL slug.

    Only ASCII letters and digits are kept; every run of other characters
    collapses to a single ``-``.  Leading and trailing ``-`` are removed,
    and text with no letters or digits yields an empty string.
    """
    if not text:
        return ""
    return _NON_ASCII_ALNUM.sub("-", text.lower()).strip("-")