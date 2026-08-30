"""Step-name normalization rules."""

import re


def normalize_step(step: str) -> str:
    """Return the canonical form of a step name.

    Surrounding whitespace and '/' characters are removed, and the
    remaining name is lowercased. Slashes between words stay in place.
    """
    return re.sub(r"^[/\s]+|[/\s]+$", "", step).lower()
