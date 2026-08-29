"""Fetch client importing the one timeout constant."""

from fetch.config import REQUEST_TIMEOUT


def fetch(url: str) -> dict[str, object]:
    """Return a canned response shape for ``url`` with the effective timeout."""
    return {"url": url, "timeout": REQUEST_TIMEOUT}
