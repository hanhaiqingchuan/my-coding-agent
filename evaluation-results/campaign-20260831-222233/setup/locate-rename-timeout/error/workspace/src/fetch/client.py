"""Fetch client keeping its own hard-coded timeout."""

REQUEST_TIMEOUT = 45


def fetch(url: str) -> dict[str, object]:
    """Return a canned response shape for ``url`` with the effective timeout."""
    return {"url": url, "timeout": REQUEST_TIMEOUT}
