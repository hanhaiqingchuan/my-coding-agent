"""Offline browser-test server with deterministic model behavior and restart control."""

from __future__ import annotations

import asyncio
import inspect
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI

from coding_agent.api.app import create_app
from coding_agent.config import load_settings
from coding_agent.context import Compactor, ContextBuilder
from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.models import (
    AssistantTurn,
    ModelStopReason,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolResult,
    ToolUsePart,
    Usage,
)
from coding_agent.main import RuntimeDependencies, build_run_coordinator
from coding_agent.model.protocol import (
    DeltaSink,
    ModelRequest,
    TextDelta,
    ThinkingBlockClosed,
    ThinkingBlockClosedSink,
    ThinkingDelta,
    ThinkingDeltaSink,
)
from coding_agent.runtime.approval import ApprovalGate
from coding_agent.runtime.publisher import EventPublisher
from coding_agent.storage.sqlite import SQLiteStore
from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.run_command import RunCommandTool

HOST = "127.0.0.1"
PORT = 8000
DEVELOPMENT_ORIGIN = "http://127.0.0.1:5173"
MODEL_KEY_NAMES = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "GOOGLE_API_KEY",
)


@dataclass(frozen=True, slots=True)
class FixturePaths:
    root: Path
    workspace: Path
    data_dir: Path


class BrowserScriptedModel:
    """Select a deterministic response from the persisted conversation itself."""

    def __init__(self) -> None:
        # The browser test holds the scripted thinking block open until it has
        # observed the expanded state, so the auto-collapse never races the poll.
        self._thinking_released = False
        self._thinking_waiters: list[asyncio.Future[None]] = []

    def release_thinking(self) -> None:
        """Let the held-open thinking block finish and emit its close event."""
        self._thinking_released = True
        for waiter in self._thinking_waiters:
            if not waiter.done():
                waiter.set_result(None)
        self._thinking_waiters = []

    async def hold_thinking_open(self) -> None:
        """Block the close event until the test releases it (consumed once)."""
        if not self._thinking_released:
            waiter = asyncio.get_running_loop().create_future()
            self._thinking_waiters.append(waiter)
            await waiter
        self._thinking_released = False

    async def complete(
        self,
        request: ModelRequest,
        on_text_delta: DeltaSink,
        cancellation: CancellationToken,
        *,
        on_thinking_delta: ThinkingDeltaSink | None = None,
        on_thinking_block_closed: ThinkingBlockClosedSink | None = None,
    ) -> AssistantTurn:
        prompt = _initial_prompt(request)
        results = _tool_results(request)

        if prompt == "stop-flow":
            chunks = tuple(f"Streaming until stopped {index}. " for index in range(200))
            await _emit(chunks, on_text_delta, cancellation, delay=0.05)
            return _turn("This response should have been stopped.")

        if prompt == "disconnect-flow":
            chunks = (
                "Working while the browser is connected… ",
                "Backend completed while the browser was away.",
            )
            await _emit(chunks, on_text_delta, cancellation, delay=0.6)
            return _turn("".join(chunks))

        if prompt == "restart-flow":
            text = "Preparing a restart-sensitive command…"
            await _emit((text,), on_text_delta, cancellation, delay=0.25)
            return AssistantTurn(
                id=f"restart-tools-{uuid4()}",
                parts=(
                    TextPart(text),
                    ToolUsePart(
                        ToolCall(
                            f"restart-command-{uuid4()}",
                            "run_command",
                            {
                                "command": "printf started > started.txt && sleep 30",
                                "cwd": ".",
                                "reason": "exercise restart recovery",
                                "timeout_seconds": 60,
                            },
                        )
                    ),
                    ToolUsePart(
                        ToolCall(
                            f"restart-never-write-{uuid4()}",
                            "write_file",
                            {
                                "operation": "write",
                                "path": "never-started.txt",
                                "content": "this queued effect must not run\n",
                            },
                        )
                    ),
                ),
                stop_reason=ModelStopReason.TOOL_USE,
                usage=Usage(input_tokens=8, output_tokens=8),
            )

        if prompt == "timeline-flow" and len(results) == 0:
            # Two calls in one round, so call_order runs 0 and 1 here and restarts at 0 in
            # the next round; the command deliberately omits cwd and timeout_seconds.
            text = "Round one prepares two effects."
            await _emit((text,), on_text_delta, cancellation, delay=0.1)
            return AssistantTurn(
                id=f"timeline-round-1-{uuid4()}",
                parts=(
                    TextPart(text),
                    ToolUsePart(
                        ToolCall(
                            f"timeline-write-{uuid4()}",
                            "write_file",
                            {
                                "operation": "write",
                                "path": "timeline-a.txt",
                                "content": "alpha\n",
                            },
                        )
                    ),
                    ToolUsePart(
                        ToolCall(
                            f"timeline-command-{uuid4()}",
                            "run_command",
                            {"command": "printf verified > timeline-marker.txt"},
                        )
                    ),
                ),
                stop_reason=ModelStopReason.TOOL_USE,
                usage=Usage(input_tokens=8, output_tokens=8),
            )

        if prompt == "timeline-flow" and len(results) == 2:
            text = "Round two continues after the first round."
            await _emit((text,), on_text_delta, cancellation, delay=0.1)
            return AssistantTurn(
                id=f"timeline-round-2-{uuid4()}",
                parts=(
                    TextPart(text),
                    ToolUsePart(
                        ToolCall(
                            f"timeline-replace-{uuid4()}",
                            "write_file",
                            {
                                "operation": "replace",
                                "path": "timeline-a.txt",
                                "old_text": "alpha",
                                "new_text": "beta",
                            },
                        )
                    ),
                ),
                stop_reason=ModelStopReason.TOOL_USE,
                usage=Usage(input_tokens=8, output_tokens=8),
            )

        if prompt == "timeline-flow":
            return await _streamed_turn(
                "All timeline steps completed.", on_text_delta, cancellation
            )

        if prompt == "agent-flow" and len(results) == 0:
            # Round one opens with a thinking block so the browser exercises the
            # real thinking callbacks end to end (loop -> publisher -> WS).
            thinking = (
                "The scripted run should first prepare a workspace change, "
                "then verify the written file exists with a follow-up command."
            )
            await _emit_thinking(
                thinking,
                on_thinking_delta,
                on_thinking_block_closed,
                cancellation,
                delay=0.2,
                hold=self.hold_thinking_open,
            )
            text = "Preparing the workspace change…"
            await _emit((text,), on_text_delta, cancellation, delay=0.25)
            return AssistantTurn(
                id=f"write-turn-{uuid4()}",
                parts=(
                    ThinkingPart(thinking),
                    TextPart(text),
                    ToolUsePart(
                        ToolCall(
                            f"write-{uuid4()}",
                            "write_file",
                            {
                                "operation": "write",
                                "path": "agent-output.txt",
                                "content": "written by scripted model\n",
                            },
                        )
                    ),
                ),
                stop_reason=ModelStopReason.TOOL_USE,
                usage=Usage(input_tokens=8, output_tokens=8),
            )

        if prompt == "agent-flow" and len(results) == 1:
            text = "The write succeeded; I will verify it."
            await _emit((text,), on_text_delta, cancellation, delay=0.1)
            return AssistantTurn(
                id=f"command-turn-{uuid4()}",
                parts=(
                    TextPart(text),
                    ToolUsePart(
                        ToolCall(
                            f"command-{uuid4()}",
                            "run_command",
                            {
                                "command": (
                                    "test -f agent-output.txt && "
                                    "printf verified > command-marker.txt"
                                ),
                                "cwd": ".",
                                "reason": "verify the approved write",
                                "timeout_seconds": 10,
                            },
                        )
                    ),
                ),
                stop_reason=ModelStopReason.TOOL_USE,
                usage=Usage(input_tokens=8, output_tokens=8),
            )

        if prompt == "agent-flow":
            return await _streamed_turn(
                "All scripted steps completed.", on_text_delta, cancellation
            )

        return await _streamed_turn("Scripted request completed.", on_text_delta, cancellation)


async def _emit(
    chunks: tuple[str, ...],
    sink: DeltaSink,
    cancellation: CancellationToken,
    *,
    delay: float,
) -> None:
    for chunk in chunks:
        cancellation.raise_if_cancelled()
        emitted = sink(TextDelta(index=0, text=chunk))
        if inspect.isawaitable(emitted):
            await emitted
        await asyncio.sleep(delay)
    cancellation.raise_if_cancelled()


async def _emit_thinking(
    text: str,
    thinking_sink: ThinkingDeltaSink | None,
    closed_sink: ThinkingBlockClosedSink | None,
    cancellation: CancellationToken,
    *,
    delay: float,
    hold: "Callable[[], Awaitable[None]] | None" = None,
) -> None:
    """Stream one thinking block the way the provider adapter does: fixed-size
    chunks, then a single close event for the block. `hold` parks the close
    event so a browser test can observe the block mid-stream deterministically."""
    size = 18
    chunks = tuple(text[index : index + size] for index in range(0, len(text), size))
    for chunk in chunks:
        cancellation.raise_if_cancelled()
        if thinking_sink is not None:
            emitted = thinking_sink(ThinkingDelta(index=0, text=chunk))
            if inspect.isawaitable(emitted):
                await emitted
        await asyncio.sleep(delay)
    if hold is not None:
        await hold()
    if closed_sink is not None:
        emitted = closed_sink(ThinkingBlockClosed(index=0))
        if inspect.isawaitable(emitted):
            await emitted
    cancellation.raise_if_cancelled()


async def _streamed_turn(
    text: str,
    sink: DeltaSink,
    cancellation: CancellationToken,
) -> AssistantTurn:
    await _emit((text,), sink, cancellation, delay=0.1)
    return _turn(text)


def _turn(text: str) -> AssistantTurn:
    return AssistantTurn(
        id=f"text-turn-{uuid4()}",
        parts=(TextPart(text),),
        stop_reason=ModelStopReason.END_TURN,
        usage=Usage(input_tokens=8, output_tokens=8),
    )


def _initial_prompt(request: ModelRequest) -> str:
    for message in request.messages:
        if message.role != "user":
            continue
        for part in message.parts:
            if isinstance(part, TextPart):
                return part.text
    return ""


def _tool_results(request: ModelRequest) -> tuple[ToolResult, ...]:
    return tuple(
        part
        for message in request.messages
        for part in message.parts
        if isinstance(part, ToolResult)
    )


def _fixture_paths() -> FixturePaths:
    root = Path(tempfile.mkdtemp(prefix="coding-agent-e2e-"))
    workspace = root / "workspace"
    data_dir = root / "data"
    workspace.mkdir()
    data_dir.mkdir()
    return FixturePaths(root, workspace, data_dir)


def _build_app(paths: FixturePaths, generation: int, restart: asyncio.Event) -> FastAPI:
    settings = load_settings(
        None,
        {
            "server.port": PORT,
            "server.open_browser": False,
            "model.model": "scripted-e2e",
        },
        {},
    )
    store = SQLiteStore(paths.data_dir / "state.db")
    store.initialize()
    model = BrowserScriptedModel()
    publisher = EventPublisher()
    approval_gate = ApprovalGate()
    tools = ToolRegistry(
        run_command=RunCommandTool(
            default_timeout_seconds=settings.tools.command_timeout_seconds,
            output_limit_bytes=settings.tools.command_output_bytes,
            kill_grace_seconds=settings.tools.kill_grace_seconds,
            pass_env=(),
            model_api_key_env=settings.model.api_key_env,
        )
    )
    runtime = RuntimeDependencies(
        store=store,
        model=model,
        context_builder=ContextBuilder(),
        compactor=Compactor(model, store, model=settings.model.model),
        tool_registry=tools,
        approval_gate=approval_gate,
        clock=asyncio.get_running_loop().time,
        sleeper=asyncio.sleep,
        event_publisher=publisher,
    )
    coordinator = build_run_coordinator(settings, runtime)
    app = create_app(
        store,
        coordinator,
        {
            "model": settings.model.model,
            "context_window": settings.model.context_window,
            "max_output_tokens": settings.model.max_output_tokens,
            "max_rounds": settings.agent.max_rounds,
        },
        server_port=PORT,
        development_origin=DEVELOPMENT_ORIGIN,
    )

    @app.get("/__test__/state")
    async def fixture_state() -> dict[str, object]:
        output_path = paths.workspace / "agent-output.txt"
        return {
            "workspace": str(paths.workspace),
            "data_dir": str(paths.data_dir),
            "generation": generation,
            "agent_output": (
                output_path.read_text(encoding="utf-8") if output_path.exists() else None
            ),
            "command_marker": (paths.workspace / "command-marker.txt").exists(),
            "never_started_exists": (paths.workspace / "never-started.txt").exists(),
        }

    @app.post("/__test__/restart")
    async def restart_server() -> dict[str, str]:
        restart.set()
        return {"status": "restarting"}

    @app.post("/__test__/thinking/release")
    async def release_thinking() -> dict[str, str]:
        model.release_thinking()
        return {"status": "released"}

    return app


async def _serve_generation(paths: FixturePaths, generation: int) -> bool:
    restart = asyncio.Event()
    app = _build_app(paths, generation, restart)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=HOST,
            port=PORT,
            log_level="warning",
            access_log=False,
        )
    )
    serving = asyncio.create_task(server.serve(), name=f"e2e-server-{generation}")
    restarting = asyncio.create_task(restart.wait(), name=f"e2e-restart-{generation}")
    done, _ = await asyncio.wait({serving, restarting}, return_when=asyncio.FIRST_COMPLETED)
    if restarting in done:
        await asyncio.sleep(0.1)
        server.should_exit = True
        await serving
    else:
        restarting.cancel()
        await asyncio.gather(restarting, return_exceptions=True)

    current = asyncio.current_task()
    leftovers = [
        task
        for task in asyncio.all_tasks()
        if task is not current and not task.done() and task is not restarting
    ]
    for task in leftovers:
        task.cancel()
    if leftovers:
        await asyncio.gather(*leftovers, return_exceptions=True)
    return restart.is_set()


async def _supervise() -> None:
    for name in MODEL_KEY_NAMES:
        os.environ.pop(name, None)
    paths = _fixture_paths()
    generation = 1
    while await _serve_generation(paths, generation):
        generation += 1


def main() -> int:
    asyncio.run(_supervise())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
