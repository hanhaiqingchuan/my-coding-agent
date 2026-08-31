"""Fetch client that hard-codes the wrong timeout value."""

REQUEST_TIMEOUT = 30


def fetch(url: str) -> dict[str, object]:
    """Return a canned response shape for ``url`` with the effective timeout."""
    return {"url": url, "timeout": REQUEST_TIMEOUT}
