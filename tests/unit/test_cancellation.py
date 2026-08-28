from __future__ import annotations

import asyncio

import pytest

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.errors import CancellationRequested


def test_new_token_is_not_cancelled() -> None:
    """A token that starts cancelled would abort every newly created run."""
    token = CancellationToken()

    assert token.cancelled is False


def test_cancel_is_idempotent_and_visible() -> None:
    """Repeated Stop commands must leave one stable cancellation state."""
    token = CancellationToken()

    token.cancel()
    token.cancel()

    assert token.cancelled is True


def test_raise_if_cancelled_raises_structured_exception() -> None:
    """Removing this guard would let a cancelled worker begin another side effect."""
    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancellationRequested):
        token.raise_if_cancelled()


@pytest.mark.asyncio
async def test_wait_unblocks_after_cancel() -> None:
    """If wait did not unblock, retry and stream tasks could remain alive after Stop."""
    token = CancellationToken()
    waiter = asyncio.create_task(token.wait())

    await asyncio.sleep(0)
    assert waiter.done() is False

    token.cancel()
    await asyncio.wait_for(waiter, timeout=0.1)
