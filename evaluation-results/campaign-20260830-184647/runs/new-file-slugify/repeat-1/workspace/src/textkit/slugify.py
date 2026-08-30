import re


def slugify(text: str) -> str:
    """Return a URL slug for *text*.

    The result is the lowercased text with each run of characters that
    are not ASCII letters or digits replaced by one ``-``, with leading
    and trailing ``-`` removed.  Text with no ASCII letters or digits
    yields an empty string.
    """
    return re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")