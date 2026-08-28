"""Deterministic tests for the sole model-request retry owner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from coding_agent.config import ConfigurationError
from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.errors import CancellationRequested
from coding_agent.model.protocol import ModelAPIError, ModelProtocolError, ModelTransportError
from coding_agent.model.retry import RetryingInvoker


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


class FakeSleeper:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self._clock.now += delay


class BlockingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []
        self.started = asyncio.Event()

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self.started.set()
        await asyncio.Future()


class ScriptedOperation:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return str(outcome)


def invoker(
    sleeper: Callable[[float], Awaitable[None]],
    clock: FakeClock,
    *,
    random_value: float = 0.0,
    now: datetime = datetime(2026, 8, 28, tzinfo=UTC),
) -> RetryingInvoker:
    return RetryingInvoker(
        sleep=sleeper,
        monotonic=clock.monotonic,
        random=lambda: random_value,
        now=lambda: now,
    )


async def no_op_retry_sink(_: object) -> None:
    return None


@pytest.mark.asyncio
async def test_retries_typed_transport_failure_with_deterministic_backoff() -> None:
    """Removing the transport retry branch would stop the second attempt."""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    operation = ScriptedOperation([ModelTransportError(True, ConnectionError()), "ok"])

    result = await invoker(sleeper, clock).invoke(operation, CancellationToken(), no_op_retry_sink)

    assert result == "ok"
    assert operation.calls == 2
    assert sleeper.delays == [2.0]


@pytest.mark.asyncio
async def test_honors_typed_retry_after_and_reports_next_attempt_deadline() -> None:
    """Ignoring a valid server delay would retry a rate-limited call too early."""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    operation = ScriptedOperation(
        [ModelAPIError(429, "rate_limit_error", "7", retryable=True), "ok"]
    )
    notices = []

    async def on_retry(notice: object) -> None:
        notices.append(notice)

    result = await invoker(sleeper, clock).invoke(operation, CancellationToken(), on_retry)

    assert result == "ok"
    assert sleeper.delays == [7.0]
    assert len(notices) == 1
    notice = notices[0]
    assert notice.attempt == 2
    assert notice.max_attempts == 5
    assert notice.delay_seconds == 7.0
    assert notice.reason == "http_429"
    assert notice.deadline_monotonic == 107.0
    assert notice.event_payload == {
        "attempt": 2,
        "max_attempts": 5,
        "delay_seconds": 7.0,
        "reason": "http_429",
        "deadline_monotonic": 107.0,
    }


@pytest.mark.asyncio
async def test_retry_after_ms_precedes_retry_after_header() -> None:
    """Using the lower-priority seconds header would violate provider retry controls."""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    operation = ScriptedOperation(
        [
            ModelAPIError(
                429,
                "rate_limit_error",
                "7",
                retryable=True,
                retry_after_ms="2500",
            ),
            "ok",
        ]
    )

    await invoker(sleeper, clock).invoke(operation, CancellationToken(), no_op_retry_sink)

    assert sleeper.delays == [2.5]


@pytest.mark.asyncio
async def test_http_date_retry_after_uses_injected_wall_clock() -> None:
    """Parsing an HTTP date against system time would make retry timing nondeterministic."""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    now = datetime(2026, 8, 28, tzinfo=UTC)
    retry_after = format_datetime(now + timedelta(seconds=7), usegmt=True)
    operation = ScriptedOperation([ModelAPIError(503, "api_error", retry_after, True), "ok"])

    await invoker(sleeper, clock, now=now).invoke(operation, CancellationToken(), no_op_retry_sink)

    assert sleeper.delays == [7.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 409, 429, 500, 529])
async def test_retries_each_retryable_http_status(status: int) -> None:
    """Dropping any required status from adapter classification would end a recoverable run."""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    operation = ScriptedOperation([ModelAPIError(status, "api_error", None, True), "ok"])

    result = await invoker(sleeper, clock).invoke(operation, CancellationToken(), no_op_retry_sink)

    assert result == "ok"
    assert operation.calls == 2
    assert sleeper.delays == [2.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", ["invalid", "61", "-1"])
async def test_invalid_or_too_large_server_delay_falls_back_to_local_backoff(
    retry_after: str,
) -> None:
    """Trusting malformed or excessive server values could leave a run stuck waiting."""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    operation = ScriptedOperation([ModelAPIError(529, "overloaded_error", retry_after, True), "ok"])

    await invoker(sleeper, clock).invoke(operation, CancellationToken(), no_op_retry_sink)

    assert sleeper.delays == [2.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after_ms", ["invalid", "61000", "-1"])
async def test_invalid_or_too_large_retry_after_ms_falls_back_to_local_backoff(
    retry_after_ms: str,
) -> None:
    """An unusable higher-priority milliseconds header must not be trusted or bypassed."""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    operation = ScriptedOperation(
        [
            ModelAPIError(
                429,
                "rate_limit_error",
                "7",
                retryable=True,
                retry_after_ms=retry_after_ms,
            ),
            "ok",
        ]
    )

    await invoker(sleeper, clock).invoke(operation, CancellationToken(), no_op_retry_sink)

    assert sleeper.delays == [2.0]


@pytest.mark.asyncio
async def test_exponential_backoff_is_capped_and_has_bounded_jitter() -> None:
    """Changing the cap or jitter branch would make repeated failures wait the wrong amount."""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    failure = ModelTransportError(True, ConnectionError())
    operation = ScriptedOperation([failure, failure, failure, failure, "ok"])

    result = await invoker(sleeper, clock, random_value=1.0).invoke(
        operation, CancellationToken(), no_op_retry_sink
    )

    assert result == "ok"
    assert sleeper.delays == [2.5, 5.0, 10.0, 20.0]


@pytest.mark.asyncio
async def test_stops_after_five_total_attempts() -> None:
    """Treating max_attempts as retries instead of total attempts would make a sixth call."""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    failure = ModelTransportError(True, ConnectionError())
    operation = ScriptedOperation([failure, failure, failure, failure, failure])

    with pytest.raises(ModelTransportError):
        await invoker(sleeper, clock).invoke(operation, CancellationToken(), no_op_retry_sink)

    assert operation.calls == 5
    assert sleeper.delays == [2.0, 4.0, 8.0, 16.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ModelAPIError(401, "authentication_error", None, retryable=False),
        ModelAPIError(403, "permission_error", None, retryable=False),
        ModelAPIError(400, "refusal", None, retryable=False),
        ModelProtocolError("BAD_STREAM", "broken structured frame"),
        ConfigurationError("model configuration is invalid"),
        RuntimeError("429 please retry"),
    ],
)
async def test_non_retryable_or_untyped_errors_are_not_classified_from_text(
    error: Exception,
) -> None:
    """Matching arbitrary exception prose would retry configuration and protocol failures."""
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    operation = ScriptedOperation([error])

    with pytest.raises(type(error)):
        await invoker(sleeper, clock).invoke(operation, CancellationToken(), no_op_retry_sink)

    assert operation.calls == 1
    assert sleeper.delays == []


@pytest.mark.asyncio
async def test_cancellation_interrupts_a_pending_retry_wait() -> None:
    """Polling only between retry waits would leave Stop blocked until the delay ends."""
    clock = FakeClock()
    sleeper = BlockingSleeper()
    cancellation = CancellationToken()
    operation = ScriptedOperation([ModelTransportError(True, ConnectionError()), "unexpected"])
    task = asyncio.create_task(
        invoker(sleeper, clock).invoke(operation, cancellation, no_op_retry_sink)
    )
    await sleeper.started.wait()

    cancellation.cancel()

    with pytest.raises(CancellationRequested):
        await asyncio.wait_for(task, timeout=1)
    assert operation.calls == 1
