"""Fetch client carrying its own copy of the timeout constant."""

TIMEOUT_SECONDS = 30


def fetch(url: str) -> dict[str, object]:
    """Return a canned response shape for ``url`` with the effective timeout."""
    return {"url": url, "timeout": TIMEOUT_SECONDS}
