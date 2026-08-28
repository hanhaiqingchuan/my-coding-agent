"""A deterministic, queue-driven implementation of the model gateway for tests."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.models import AssistantTurn, TextPart
from coding_agent.model.protocol import DeltaSink, ModelRequest, TextDelta


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
    ) -> AssistantTurn:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        return self.turn


__all__.append("BlockingModel")
