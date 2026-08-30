"""Application composition root shared by browser and headless delivery."""

from __future__ import annotations

import asyncio
import json
import os
import time
import webbrowser
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import uvicorn

from coding_agent.api.app import create_app
from coding_agent.config import AppSettings, ConfigurationError, resolve_api_key
from coding_agent.context import Compactor, ContextBuilder
from coding_agent.core.models import RunState
from coding_agent.model import ModelGateway
from coding_agent.model.anthropic_messages import AnthropicMessagesModel
from coding_agent.model.retry import RetryingInvoker
from coding_agent.runtime.approval import ApprovalGate
from coding_agent.runtime.coordinator import RunCoordinator, RunMutationGate
from coding_agent.runtime.loop import AgentLoop
from coding_agent.runtime.metrics import build_run_report
from coding_agent.runtime.publisher import EventPublisher
from coding_agent.storage.sqlite import SQLiteStore
from coding_agent.tools.paths import WorkspaceBoundary
from coding_agent.tools.read_file import ReadFileTool
from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.run_command import AllowedCommand, CommandPolicy, RunCommandTool

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


@dataclass(slots=True)
class RuntimeDependencies:
    """All stateful runtime boundaries; tests replace them without hidden globals."""

    store: SQLiteStore
    model: ModelGateway
    context_builder: ContextBuilder
    compactor: Compactor
    tool_registry: ToolRegistry
    approval_gate: ApprovalGate
    clock: Clock
    sleeper: Sleeper
    event_publisher: EventPublisher


def load_command_policy(path: Path, workspace: Path) -> CommandPolicy:
    """Load a strict v1 exact-command policy and canonicalize each relative cwd."""
    boundary = WorkspaceBoundary(workspace)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigurationError(f"command_policy: file not found: {path}") from error
    except (OSError, UnicodeError) as error:
        raise ConfigurationError("command_policy: unable to read UTF-8 JSON") from error
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"command_policy: invalid JSON: {error.msg}") from error

    if not isinstance(raw, dict) or set(raw) != {"schema_version", "allowed"}:
        raise ConfigurationError("command_policy: expected only schema_version and allowed fields")
    schema_version = raw["schema_version"]
    entries = raw["allowed"]
    if schema_version != "command-policy-v1":
        raise ConfigurationError("command_policy.schema_version: must be command-policy-v1")
    if not isinstance(entries, list):
        raise ConfigurationError("command_policy.allowed: must be an array")

    allowed: list[AllowedCommand] = []
    for index, entry in enumerate(entries):
        field = f"command_policy.allowed[{index}]"
        if not isinstance(entry, dict) or set(entry) != {"command", "cwd"}:
            raise ConfigurationError(f"{field}: expected only command and cwd fields")
        command = entry["command"]
        cwd = entry["cwd"]
        if not isinstance(command, str) or not command.strip():
            raise ConfigurationError(f"{field}.command: must be a non-empty exact string")
        if not isinstance(cwd, str) or not cwd:
            raise ConfigurationError(f"{field}.cwd: must be a non-empty relative path")
        cwd_path = Path(cwd)
        if cwd_path.is_absolute() or ".." in cwd_path.parts:
            raise ConfigurationError(f"{field}.cwd: must remain workspace-relative")
        try:
            canonical_cwd = boundary.resolve(cwd)
        except ValueError as error:
            raise ConfigurationError(f"{field}.cwd: {error}") from error
        if not canonical_cwd.is_dir():
            raise ConfigurationError(f"{field}.cwd: must name a workspace directory")
        relative = canonical_cwd.relative_to(boundary.root).as_posix()
        allowed.append(AllowedCommand(command, relative or "."))
    return CommandPolicy(schema_version=schema_version, allowed=allowed)


def build_runtime_dependencies(
    *,
    settings: AppSettings,
    workspace: Path,
    data_dir: Path,
    auto_approve: bool,
    command_policy: CommandPolicy | None,
    environ: Mapping[str, str] | None = None,
) -> RuntimeDependencies:
    """Construct production adapters only at the application boundary."""
    WorkspaceBoundary(workspace)
    api_key = resolve_api_key(settings, environ if environ is not None else os.environ)
    data_dir.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore(data_dir / "state.db")
    store.initialize()
    model = AnthropicMessagesModel(settings.model, api_key)
    tools = ToolRegistry(
        read_file=ReadFileTool(settings.tools),
        run_command=RunCommandTool(
            default_timeout_seconds=settings.tools.command_timeout_seconds,
            output_limit_bytes=settings.tools.command_output_bytes,
            kill_grace_seconds=settings.tools.kill_grace_seconds,
            pass_env=settings.tools.pass_env,
            model_api_key_env=settings.model.api_key_env,
            command_policy=command_policy,
        ),
    )
    return RuntimeDependencies(
        store=store,
        model=model,
        context_builder=ContextBuilder(),
        compactor=Compactor(model, store, model=settings.model.model),
        tool_registry=tools,
        approval_gate=ApprovalGate(auto_approve=auto_approve),
        clock=time.monotonic,
        sleeper=asyncio.sleep,
        event_publisher=EventPublisher(),
    )


async def run_headless(
    workspace: Path,
    data_dir: Path,
    prompt_file: Path,
    report_out: Path,
    settings: AppSettings,
    *,
    dependencies: RuntimeDependencies | None = None,
    auto_approve: bool = False,
    command_policy: CommandPolicy | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Run the production AgentLoop without browser delivery and write a v1 report."""
    boundary = WorkspaceBoundary(workspace)
    prompt = _read_prompt(prompt_file)
    runtime = dependencies or build_runtime_dependencies(
        settings=settings,
        workspace=boundary.root,
        data_dir=data_dir,
        auto_approve=auto_approve,
        command_policy=command_policy,
        environ=environ,
    )
    runtime.approval_gate.auto_approve = auto_approve
    runtime.tool_registry.configure_command_policy(command_policy)
    runtime.store.recover_interrupted_runs()
    coordinator = build_run_coordinator(settings, runtime)
    session = runtime.store.create_session(str(boundary.root), prompt_file.name)
    started = runtime.clock()
    run = await coordinator.start_run(session.id, prompt, f"headless-start-{uuid4()}")
    finished = await coordinator.wait_for_run(run.id)
    _write_run_report(
        report_out,
        build_run_report(
            runtime.store,
            finished,
            tool_schemas=runtime.tool_registry.schemas(),
            agent_monotonic_ms=max(0, round((runtime.clock() - started) * 1000)),
        ),
    )
    return 0 if finished.state is RunState.COMPLETED else 1


def build_run_coordinator(
    settings: AppSettings,
    runtime: RuntimeDependencies,
) -> RunCoordinator:
    """Wire the one AgentLoop used by every delivery mode into its sole task owner."""
    invoker = RetryingInvoker(
        max_attempts=settings.retry.max_attempts,
        initial_delay_seconds=settings.retry.initial_delay_seconds,
        max_delay_seconds=settings.retry.max_delay_seconds,
        jitter_ratio=settings.retry.jitter_ratio,
        sleep=runtime.sleeper,
        monotonic=runtime.clock,
    )
    mutation_gate = RunMutationGate(runtime.store, runtime.event_publisher)
    loop = AgentLoop(
        store=runtime.store,
        context_builder=runtime.context_builder,
        compactor=runtime.compactor,
        model=runtime.model,
        invoker=invoker,
        tools=runtime.tool_registry,
        approval_gate=runtime.approval_gate,
        publisher=runtime.event_publisher,
        mutation_gate=mutation_gate,
        settings=settings,
    )
    return RunCoordinator(
        store=runtime.store,
        mutation_gate=mutation_gate,
        runner=loop,
        config_snapshot=asdict(settings),
        approval_gate=runtime.approval_gate,
        session_compactor=loop,
    )


def serve_web(
    *,
    settings: AppSettings,
    workspace: Path | None,
    data_dir: Path | None,
    dependencies: RuntimeDependencies | None = None,
    auto_approve: bool = False,
    evaluation_results_root: Path | None = None,
) -> int:
    """Run the local browser API on the fixed loopback interface."""
    runtime_workspace = WorkspaceBoundary(workspace or Path.cwd()).root
    runtime_data_dir = data_dir or Path.cwd() / ".coding-agent"
    runtime = dependencies or build_runtime_dependencies(
        settings=settings,
        workspace=runtime_workspace,
        data_dir=runtime_data_dir,
        auto_approve=auto_approve,
        command_policy=None,
    )
    runtime.approval_gate.auto_approve = auto_approve
    if workspace is not None and not any(
        session.workspace_realpath == str(runtime_workspace)
        for session in runtime.store.list_sessions()
    ):
        runtime.store.create_session(str(runtime_workspace), runtime_workspace.name)
    coordinator = build_run_coordinator(settings, runtime)
    public_config = {
        "model": settings.model.model,
        "context_window": settings.model.context_window,
        "max_output_tokens": settings.model.max_output_tokens,
        "max_rounds": settings.agent.max_rounds,
    }
    app = create_app(
        runtime.store,
        coordinator,
        public_config,
        server_port=settings.server.port,
        web_dist=Path(__file__).resolve().parents[2] / "web" / "dist",
        evaluation_results_root=(
            evaluation_results_root or runtime_data_dir / "evaluation-results"
        ),
    )
    if settings.server.open_browser:
        webbrowser.open(f"http://127.0.0.1:{settings.server.port}")
    uvicorn.run(app, host="127.0.0.1", port=settings.server.port, log_level="info")
    return 0


def _read_prompt(prompt_file: Path) -> str:
    try:
        prompt = prompt_file.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConfigurationError(f"prompt_file: file not found: {prompt_file}") from error
    except (OSError, UnicodeError) as error:
        raise ConfigurationError("prompt_file: unable to read UTF-8 text") from error
    if not prompt.strip():
        raise ConfigurationError("prompt_file: must contain a non-empty prompt")
    return prompt


def _write_run_report(report_out: Path, report: Mapping[str, object]) -> None:
    try:
        report_out.parent.mkdir(parents=True, exist_ok=True)
        report_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise ConfigurationError("report_out: unable to write run report") from error


__all__ = [
    "RuntimeDependencies",
    "build_run_coordinator",
    "build_runtime_dependencies",
    "load_command_policy",
    "run_headless",
    "serve_web",
]
