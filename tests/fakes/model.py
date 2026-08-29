"""A deterministic, queue-driven implementation of the model gateway for tests."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.models import AssistantTurn, TextPart, ThinkingPart
from coding_agent.model.protocol import (
    DeltaSink,
    ModelRequest,
    TextDelta,
    ThinkingBlockClosed,
    ThinkingBlockClosedSink,
    ThinkingDelta,
    ThinkingDeltaSink,
)


class ScriptedModel:
    def __init__(self, script: Sequence[AssistantTurn | Exception]) -> None:
        self._script = list(script)
        self.requests: list[ModelRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    async def complete(
        self,
        request: ModelRequest,
        on_text_delta: DeltaSink,
        cancellation: CancellationToken,
        *,
        on_thinking_delta: ThinkingDeltaSink | None = None,
        on_thinking_block_closed: ThinkingBlockClosedSink | None = None,
    ) -> AssistantTurn:
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        if not self._script:
            raise AssertionError("ScriptedModel received more requests than scripted outcomes")
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        for index, part in enumerate(outcome.parts):
            cancellation.raise_if_cancelled()
            if isinstance(part, TextPart):
                result = on_text_delta(TextDelta(index=index, text=part.text))
                if inspect.isawaitable(result):
                    await result
            elif isinstance(part, ThinkingPart):
                # Mirror the adapter: two reasoning chunks, then one close per block.
                middle = len(part.text) // 2
                for chunk in (part.text[:middle], part.text[middle:]):
                    if on_thinking_delta is not None and chunk:
                        result = on_thinking_delta(ThinkingDelta(index=index, text=chunk))
                        if inspect.isawaitable(result):
                            await result
                if on_thinking_block_closed is not None:
                    result = on_thinking_block_closed(ThinkingBlockClosed(index=index))
                    if inspect.isawaitable(result):
                        await result
        return outcome


__all__ = ["ScriptedModel"]


class BlockingModel:
    """Return a scripted turn only after the test releases an in-flight request."""

    def __init__(self, turn: AssistantTurn) -> None:
        self.turn = turn
        self.requests: list[ModelRequest] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def call_count(self) -> int:
        return len(self.requests)

    async def complete(
        self,
        request: ModelRequest,
        on_text_delta: DeltaSink,
        cancellation: CancellationToken,
        *,
        on_thinking_delta: ThinkingDeltaSink | None = None,
        on_thinking_block_closed: ThinkingBlockClosedSink | None = None,
    ) -> AssistantTurn:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        return self.turn


__all__.append("BlockingModel")
