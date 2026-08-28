from __future__ import annotations

import asyncio
from collections.abc import Callable

from coding_agent.core.models import PreparedToolCall, ToolCall, ToolResult
from coding_agent.tools import ToolContext, error_result, result
from coding_agent.tools.paths import WorkspaceBoundary


class RecordingTools:
    """Deterministic tool boundary that records execution of prepared calls."""

    def __init__(self, before_execute: Callable[[PreparedToolCall], None] | None = None) -> None:
        self.before_execute = before_execute
        self.executed: list[str] = []

    @property
    def execution_count(self) -> int:
        return len(self.executed)

    def schemas(self) -> list[dict[str, object]]:
        return [
            {
                "name": name,
                "description": name,
                "input_schema": {"type": "object", "additionalProperties": True},
            }
            for name in ("read_file", "write_file", "run_command")
        ]

    def prepare(
        self, call: ToolCall, workspace: WorkspaceBoundary
    ) -> PreparedToolCall | ToolResult:
        if call.name not in {"read_file", "write_file", "run_command"}:
            return error_result(call.id, call.name, "UNKNOWN_TOOL", f"unknown tool: {call.name}")
        if call.input.get("invalid") is True:
            return error_result(call.id, call.name, "INVALID_ARGUMENT", "invalid scripted input")
        return PreparedToolCall(
            call=call,
            requires_approval=call.name in {"write_file", "run_command"},
            target=str(workspace.root / f"{call.id}.target"),
            preview="scripted diff" if call.name == "write_file" else None,
            metadata={"scripted": True},
        )

    async def execute(self, prepared: PreparedToolCall, context: ToolContext) -> ToolResult:
        context.cancellation.raise_if_cancelled()
        if self.before_execute is not None:
            self.before_execute(prepared)
        self.executed.append(prepared.call.id)
        await context.emit_output(f"output:{prepared.call.id}")
        return result(
            prepared.call.id,
            prepared.call.name,
            ok=True,
            summary=f"executed {prepared.call.name}",
            data={"call_id": prepared.call.id},
        )


__all__ = ["RecordingTools"]


class BlockingTools(RecordingTools):
    """Expose the moment after effect-start while allowing a realistic late result."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, prepared: PreparedToolCall, context: ToolContext) -> ToolResult:
        self.executed.append(prepared.call.id)
        self.started.set()
        await self.release.wait()
        return result(
            prepared.call.id,
            prepared.call.name,
            ok=True,
            summary="effect completed before cancellation was observed",
        )


__all__.append("BlockingTools")


class CancellationRaisingTools(RecordingTools):
    """Model a tool that cooperatively aborts after its effect-start marker."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def execute(self, prepared: PreparedToolCall, context: ToolContext) -> ToolResult:
        self.executed.append(prepared.call.id)
        self.started.set()
        await context.cancellation.wait()
        context.cancellation.raise_if_cancelled()
        raise AssertionError("cancelled tool must not continue")


__all__.append("CancellationRaisingTools")
