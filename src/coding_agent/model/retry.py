"""Cancellable, deterministic model-request retry policy."""

from __future__ import annotations

import asyncio
import math
import random as random_module
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TypeAlias, TypeVar

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.errors import CancellationRequested
from coding_agent.core.events import retry_wait_payload
from coding_agent.model.protocol import ModelAPIError, ModelTransportError

T = TypeVar("T")

AsyncOperation: TypeAlias = Callable[[], Awaitable[T]]
RetrySink: TypeAlias = Callable[["RetryNotice"], Awaitable[None]]
Sleep: TypeAlias = Callable[[float], Awaitable[None]]
MonotonicClock: TypeAlias = Callable[[], float]
RandomSource: TypeAlias = Callable[[], float]
WallClock: TypeAlias = Callable[[], datetime]

_MAX_SERVER_DELAY_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class RetryNotice:
    """Visible, durable data emitted immediately before an interruptible wait."""

    attempt: int
    max_attempts: int
    delay_seconds: float
    reason: str
    deadline_monotonic: float

    @property
    def event_payload(self) -> dict[str, int | float | str]:
        return retry_wait_payload(
            attempt=self.attempt,
            max_attempts=self.max_attempts,
            delay_seconds=self.delay_seconds,
            reason=self.reason,
            deadline_monotonic=self.deadline_monotonic,
        )


@dataclass(frozen=True, slots=True)
class _RetryDecision:
    reason: str
    retry_after_ms: str | None = None
    retry_after: str | None = None


class RetryingInvoker:
    """The only owner of retry attempts for a normalized model operation."""

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        initial_delay_seconds: float = 2.0,
        max_delay_seconds: float = 30.0,
        jitter_ratio: float = 0.25,
        sleep: Sleep = asyncio.sleep,
        monotonic: MonotonicClock = time.monotonic,
        random: RandomSource = random_module.random,
        now: WallClock = lambda: datetime.now(UTC),
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if initial_delay_seconds <= 0 or max_delay_seconds <= 0:
            raise ValueError("retry delays must be positive")
        if max_delay_seconds < initial_delay_seconds:
            raise ValueError("max delay must be at least initial delay")
        if not 0 <= jitter_ratio <= 1:
            raise ValueError("jitter ratio must be between zero and one")
        self._max_attempts = max_attempts
        self._initial_delay_seconds = initial_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._jitter_ratio = jitter_ratio
        self._sleep = sleep
        self._monotonic = monotonic
        self._random = random
        self._now = now

    async def invoke(
        self,
        operation: AsyncOperation[T],
        cancellation: CancellationToken,
        on_retry: RetrySink,
    ) -> T:
        """Run an operation, retrying only structured retryable model failures."""
        for attempt in range(1, self._max_attempts + 1):
            cancellation.raise_if_cancelled()
            try:
                return await operation()
            except Exception as error:
                decision = _retry_decision(error)
                if decision is None or attempt == self._max_attempts:
                    raise
                delay_seconds = self._server_delay(decision)
                if delay_seconds is None:
                    delay_seconds = self._backoff_delay(attempt)
                notice = RetryNotice(
                    attempt=attempt + 1,
                    max_attempts=self._max_attempts,
                    delay_seconds=delay_seconds,
                    reason=decision.reason,
                    deadline_monotonic=self._monotonic() + delay_seconds,
                )
                await on_retry(notice)
                await self._wait(delay_seconds, cancellation)
        raise AssertionError("retry loop must return or raise")  # pragma: no cover

    def _server_delay(self, decision: _RetryDecision) -> float | None:
        if decision.retry_after_ms is not None:
            return _milliseconds_delay(decision.retry_after_ms)
        if decision.retry_after is None:
            return None
        return _retry_after_delay(decision.retry_after, self._now())

    def _backoff_delay(self, failed_attempt: int) -> float:
        base_delay = min(
            self._initial_delay_seconds * (2 ** (failed_attempt - 1)), self._max_delay_seconds
        )
        jitter = base_delay * self._jitter_ratio * self._random()
        return min(base_delay + jitter, self._max_delay_seconds)

    async def _wait(self, delay_seconds: float, cancellation: CancellationToken) -> None:
        cancellation.raise_if_cancelled()
        sleep_task = asyncio.create_task(self._sleep(delay_seconds))
        cancellation_task = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {sleep_task, cancellation_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancellation_task in done:
                sleep_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sleep_task
                raise CancellationRequested()
            cancellation_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancellation_task
            await sleep_task
        finally:
            for task in (sleep_task, cancellation_task):
                if not task.done():
                    task.cancel()


def _retry_decision(error: BaseException) -> _RetryDecision | None:
    """Classify only the typed errors produced by the model protocol boundary."""
    if isinstance(error, ModelTransportError) and error.retryable:
        return _RetryDecision(reason="transport")
    if isinstance(error, ModelAPIError) and error.retryable:
        return _RetryDecision(
            reason=f"http_{error.status_code}",
            retry_after_ms=error.retry_after_ms,
            retry_after=error.retry_after,
        )
    return None


def _milliseconds_delay(value: str) -> float | None:
    try:
        milliseconds = float(value)
    except ValueError:
        return None
    return _valid_server_delay(milliseconds / 1000)


def _retry_after_delay(value: str, now: datetime) -> float | None:
    try:
        seconds = float(value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if parsed.tzinfo is None:
            return None
        seconds = (parsed.astimezone(UTC) - now.astimezone(UTC)).total_seconds()
    return _valid_server_delay(seconds)


def _valid_server_delay(seconds: float) -> float | None:
    if not math.isfinite(seconds) or not 0 <= seconds <= _MAX_SERVER_DELAY_SECONDS:
        return None
    return seconds


__all__ = ["AsyncOperation", "RetryNotice", "RetrySink", "RetryingInvoker"]
