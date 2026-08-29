"""File-name helpers."""


def with_suffix(name: str, suffix: str) -> str:
    """Append ``suffix`` unless a case-insensitive copy of it already ends the name."""
    if name.lower().endswith(suffix.lower()):
        return name
    return name + suffix


def drop_prefix(name: str, prefix: str) -> str:
    """Return ``name`` without ``prefix`` when it starts with it."""
    return name[len(prefix) :] if name.startswith(prefix) else name
