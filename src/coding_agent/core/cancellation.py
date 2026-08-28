"""A small cooperative cancellation primitive for core and tool code."""

from __future__ import annotations

import asyncio

from coding_agent.core.errors import CancellationRequested


class CancellationToken:
    """Signal cancellation without coupling the core to a web or persistence layer."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        """Request cancellation. Repeated calls intentionally have no additional effect."""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Stop the current cooperative operation at a safe checkpoint."""
        if self.cancelled:
            raise CancellationRequested()

    async def wait(self) -> None:
        """Wait until another task requests cancellation."""
        await self._event.wait()
