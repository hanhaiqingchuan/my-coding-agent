"""Fetch client carrying its own copy of the timeout constant."""

from .config import REQUEST_TIMEOUT


def fetch(url: str) -> dict[str, object]:
    """Return a canned response shape for ``url`` with the effective timeout."""
    return {"url": url, "timeout": REQUEST_TIMEOUT}
