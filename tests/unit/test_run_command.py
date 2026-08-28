from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
from pathlib import Path

import pytest

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.models import PreparedToolCall, ToolCall
from coding_agent.tools import ToolContext
from coding_agent.tools.paths import WorkspaceBoundary
from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.run_command import AllowedCommand, CommandPolicy, RunCommandTool


def python_command(source: str) -> str:
    return f"uv run --python 3.12 python -c {shlex.quote(source)}"


def prepared_command(
    tool: RunCommandTool,
    workspace: WorkspaceBoundary,
    command: str,
    **arguments: object,
) -> PreparedToolCall:
    prepared = tool.prepare(
        ToolCall("command-call", "run_command", {"command": command, **arguments}),
        workspace,
    )
    assert prepared.requires_approval is True
    return prepared


async def execute_command(
    tool: RunCommandTool,
    workspace: WorkspaceBoundary,
    command: str,
    *,
    cancellation: CancellationToken | None = None,
    **arguments: object,
) -> tuple[object, str]:
    emitted: list[str] = []

    async def emit_output(text: str) -> None:
        emitted.append(text)

    result = await tool.execute(
        prepared_command(tool, workspace, command, **arguments),
        ToolContext(workspace, cancellation or CancellationToken(), emit_output),
    )
    return result, "".join(emitted)


@pytest.mark.asyncio
async def test_command_uses_workspace_root_and_canonical_subdirectory(tmp_path: Path) -> None:
    """Dropping or misresolving cwd would run an approved command in the wrong directory."""
    workspace = WorkspaceBoundary(tmp_path)
    subdirectory = tmp_path / "nested"
    subdirectory.mkdir()
    tool = RunCommandTool()

    root_result, _ = await execute_command(tool, workspace, "pwd")
    nested_result, _ = await execute_command(tool, workspace, "pwd", cwd="nested")

    assert root_result.ok is True
    assert root_result.data["output"] == f"{workspace.root}\n"
    assert nested_result.ok is True
    assert nested_result.data["output"] == f"{subdirectory.resolve()}\n"


def test_command_rejects_cwd_outside_workspace(tmp_path: Path) -> None:
    """Skipping containment would allow an approved workspace command to escape its session."""
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    result = ToolRegistry().prepare(
        ToolCall("outside", "run_command", {"command": "pwd", "cwd": str(outside)}),
        WorkspaceBoundary(workspace_path),
    )

    assert not isinstance(result, PreparedToolCall)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "PATH_OUTSIDE_WORKSPACE"


def test_command_rejects_non_integer_timeout(tmp_path: Path) -> None:
    """Accepting a float behind the integer schema would make validation path-dependent."""
    result = ToolRegistry().prepare(
        ToolCall(
            "float-timeout",
            "run_command",
            {"command": "pwd", "timeout_seconds": 0.5},
        ),
        WorkspaceBoundary(tmp_path),
    )

    assert not isinstance(result, PreparedToolCall)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_command_merges_stdout_and_stderr_and_reports_nonzero_exit(tmp_path: Path) -> None:
    """Losing stderr or the exit status would make a failed verification look successful."""
    result, emitted = await execute_command(
        RunCommandTool(),
        WorkspaceBoundary(tmp_path),
        "printf stdout; printf stderr >&2; exit 7",
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "COMMAND_FAILED"
    assert result.data["exit_code"] == 7
    assert result.data["output"] == "stdoutstderr"
    assert emitted == result.data["output"]


@pytest.mark.asyncio
async def test_command_uses_one_bounded_buffer_but_drains_all_output(tmp_path: Path) -> None:
    """Stopping reads at the cap can deadlock the child or diverge UI and stored output."""
    limit = 40 * 1024
    extra = 317
    command = python_command(
        "import sys; "
        f"sys.stdout.buffer.write(b'A' * {limit}); sys.stdout.buffer.flush(); "
        f"sys.stderr.buffer.write(b'B' * {extra}); sys.stderr.buffer.flush()"
    )

    result, emitted = await execute_command(
        RunCommandTool(output_limit_bytes=limit), WorkspaceBoundary(tmp_path), command
    )

    assert result.ok is True
    assert result.truncated is True
    assert result.data["output_bytes"] == limit + extra
    assert result.data["output"] == "A" * limit
    assert emitted == result.data["output"]
    assert len(emitted.encode()) == limit


@pytest.mark.asyncio
async def test_command_timeout_terminates_process(tmp_path: Path) -> None:
    """A timeout that only returns to the caller would leave the command running."""
    tool = RunCommandTool(default_timeout_seconds=0.1, kill_grace_seconds=0.05)
    started = time.monotonic()

    result, _ = await execute_command(
        tool,
        WorkspaceBoundary(tmp_path),
        python_command("import time; time.sleep(30)"),
    )

    assert time.monotonic() - started < 3
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "COMMAND_TIMEOUT"
    assert result.data["timed_out"] is True


async def wait_for_file(path: Path) -> None:
    for _ in range(200):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path.name}")


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def assert_processes_exit(pids: list[int]) -> None:
    for _ in range(200):
        if not any(process_exists(pid) for pid in pids):
            return
        await asyncio.sleep(0.01)
    assert not [pid for pid in pids if process_exists(pid)]


@pytest.mark.asyncio
async def test_stop_terminates_command_process_group(tmp_path: Path) -> None:
    """Signalling only the shell would orphan the command and its grandchild after Stop."""
    pid_file = tmp_path / "processes.json"
    source = (
        "import json, pathlib, signal, subprocess, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "child=subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']); "
        "pathlib.Path('processes.json').write_text("
        "json.dumps([__import__('os').getpid(), child.pid]), encoding='utf-8'); "
        "time.sleep(30)"
    )
    cancellation = CancellationToken()
    workspace = WorkspaceBoundary(tmp_path)
    emitted: list[str] = []

    async def emit_output(text: str) -> None:
        emitted.append(text)

    tool = RunCommandTool(default_timeout_seconds=10, kill_grace_seconds=0.05)
    task = asyncio.create_task(
        tool.execute(
            prepared_command(tool, workspace, python_command(source)),
            ToolContext(workspace, cancellation, emit_output),
        )
    )
    await wait_for_file(pid_file)
    pids = json.loads(pid_file.read_text(encoding="utf-8"))
    cancellation.cancel()
    result = await asyncio.wait_for(task, timeout=3)

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "COMMAND_CANCELLED"
    assert result.data["cancelled"] is True
    await assert_processes_exit(pids)


@pytest.mark.asyncio
async def test_child_environment_is_minimal_and_model_key_is_never_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inheriting the parent environment can disclose credentials to model-chosen commands."""
    secret_names = (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "UNLIKELY_SENTINEL_SECRET",
        "CUSTOM_MODEL_KEY",
    )
    for name in secret_names:
        monkeypatch.setenv(name, f"fake-value-for-{name.lower()}")
    monkeypatch.setenv("EXPLICIT_SAFE_VALUE", "visible")
    parent_home = os.environ.get("HOME")
    names_literal = repr(secret_names)
    source = (
        "import json, os; "
        f"names={names_literal}; "
        "print(json.dumps({"
        "'secret_presence': {name: name in os.environ for name in names}, "
        "'safe': os.environ.get('EXPLICIT_SAFE_VALUE'), "
        "'has_path': bool(os.environ.get('PATH')), "
        "'home': os.environ.get('HOME')}))"
    )
    tool = RunCommandTool(
        pass_env=("EXPLICIT_SAFE_VALUE", "CUSTOM_MODEL_KEY"),
        model_api_key_env="CUSTOM_MODEL_KEY",
    )

    result, _ = await execute_command(tool, WorkspaceBoundary(tmp_path), python_command(source))
    report = json.loads(result.data["output"])

    assert result.ok is True
    assert report["secret_presence"] == {name: False for name in secret_names}
    assert report["safe"] == "visible"
    assert report["has_path"] is True
    assert report["home"] != parent_home


@pytest.mark.asyncio
async def test_locale_prefix_does_not_bypass_environment_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allowing arbitrary LC_* names would leak a credential disguised as locale data."""
    variable = "LC_CREDENTIAL_SENTINEL"
    monkeypatch.setenv(variable, "fake-locale-prefixed-secret")
    source = f"import json, os; print(json.dumps({{'present': {variable!r} in os.environ}}))"

    result, _ = await execute_command(
        RunCommandTool(), WorkspaceBoundary(tmp_path), python_command(source)
    )

    assert result.ok is True
    assert json.loads(result.data["output"]) == {"present": False}


@pytest.mark.asyncio
async def test_command_policy_requires_exact_command_and_canonical_cwd(tmp_path: Path) -> None:
    """Matching only command text could run a headless evaluation command in the wrong cwd."""
    (tmp_path / "allowed").mkdir()
    (tmp_path / "other").mkdir()
    policy = CommandPolicy(
        schema_version="command-policy-v1",
        allowed=(AllowedCommand("pwd", "allowed"),),
    )
    tool = RunCommandTool(command_policy=policy)
    workspace = WorkspaceBoundary(tmp_path)

    allowed, _ = await execute_command(tool, workspace, "pwd", cwd="allowed")
    rejected = ToolRegistry(run_command=tool).prepare(
        ToolCall("rejected", "run_command", {"command": "pwd", "cwd": "other"}),
        workspace,
    )
    changed_command = ToolRegistry(run_command=tool).prepare(
        ToolCall("changed", "run_command", {"command": "pwd ", "cwd": "allowed"}),
        workspace,
    )

    assert allowed.ok is True
    assert not isinstance(rejected, PreparedToolCall)
    assert rejected.error is not None
    assert rejected.error.code == "COMMAND_NOT_ALLOWED"
    assert not isinstance(changed_command, PreparedToolCall)
    assert changed_command.error is not None
    assert changed_command.error.code == "COMMAND_NOT_ALLOWED"


def test_command_policy_rejects_unknown_schema_version() -> None:
    """Silently accepting a future policy schema could weaken headless evaluation controls."""
    with pytest.raises(ValueError, match="command-policy-v1"):
        CommandPolicy(schema_version="command-policy-v2", allowed=())
