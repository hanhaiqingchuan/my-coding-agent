"""URL slug helpers."""

import re


def slugify(text: str) -> str:
    """Turn *text* into a safe URL slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")