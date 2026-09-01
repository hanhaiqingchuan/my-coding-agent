"""File-name helpers."""


def with_suffix(name: str, suffix: str) -> str:
    """Return ``name`` with ``suffix`` appended unless it already ends with it."""
    if name.endswith(suffix):
        return name
    return name + suffix


def drop_prefix(name: str, prefix: str) -> str:
    """Return ``name`` without ``prefix`` when it starts with it."""
    return name[len(prefix) :] if name.startswith(prefix) else name
