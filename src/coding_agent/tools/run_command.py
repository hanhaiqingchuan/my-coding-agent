"""Bounded, non-interactive POSIX shell command execution."""

from __future__ import annotations

import asyncio
import codecs
import os
import signal
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.models import PreparedToolCall, ToolCall, ToolError, ToolResult
from coding_agent.tools import OutputSink, ToolContext, ToolInputError, result
from coding_agent.tools.paths import WorkspaceBoundary, WorkspacePathError

_LOCALE_ENV_NAMES = frozenset(
    {
        "LANG",
        "LC_ALL",
        "LC_COLLATE",
        "LC_CTYPE",
        "LC_MESSAGES",
        "LC_MONETARY",
        "LC_NUMERIC",
        "LC_TIME",
    }
)
_TEMP_ENV_NAMES = frozenset({"TMPDIR", "TMP", "TEMP"})


@dataclass(frozen=True, slots=True)
class AllowedCommand:
    """One exact command and canonical workspace-relative cwd policy entry."""

    command: str
    workspace_relative_cwd: str

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("allowed command must not be empty")
        cwd = Path(self.workspace_relative_cwd)
        if not self.workspace_relative_cwd or cwd.is_absolute() or ".." in cwd.parts:
            raise ValueError("allowed command cwd must be workspace-relative")
        object.__setattr__(self, "workspace_relative_cwd", _relative_cwd_text(cwd))


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    """A versioned allowlist used only by headless evaluation."""

    schema_version: str
    allowed: Sequence[AllowedCommand]

    def __post_init__(self) -> None:
        if self.schema_version != "command-policy-v1":
            raise ValueError("command policy schema must be command-policy-v1")
        object.__setattr__(self, "allowed", tuple(self.allowed))

    def allows(self, command: str, canonical_cwd: Path) -> bool:
        """Match exact command text and a canonical workspace-relative cwd."""
        cwd = _relative_cwd_text(canonical_cwd)
        return any(
            item.command == command and item.workspace_relative_cwd == cwd for item in self.allowed
        )


@dataclass(frozen=True, slots=True)
class _RunRequest:
    command: str
    cwd: str
    relative_cwd: str
    reason: str | None
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class _CapturedOutput:
    data: bytes
    byte_count: int


class RunCommandTool:
    """Prepare approved commands and execute them in a fresh POSIX process group."""

    name = "run_command"

    def __init__(
        self,
        *,
        default_timeout_seconds: float = 120,
        output_limit_bytes: int = 40 * 1024,
        kill_grace_seconds: float = 3,
        pass_env: Sequence[str] = (),
        model_api_key_env: str = "ANTHROPIC_API_KEY",
        command_policy: CommandPolicy | None = None,
    ) -> None:
        if default_timeout_seconds <= 0:
            raise ValueError("default command timeout must be positive")
        if output_limit_bytes <= 0:
            raise ValueError("command output limit must be positive")
        if kill_grace_seconds <= 0:
            raise ValueError("command kill grace must be positive")
        if not model_api_key_env:
            raise ValueError("model API key environment name must not be empty")
        if any(not name or "=" in name for name in pass_env):
            raise ValueError("passed environment names must be non-empty variable names")
        self.default_timeout_seconds = default_timeout_seconds
        self.output_limit_bytes = output_limit_bytes
        self.kill_grace_seconds = kill_grace_seconds
        self.pass_env = tuple(pass_env)
        self.model_api_key_env = model_api_key_env
        self.command_policy = command_policy

    def prepare(self, call: ToolCall, workspace: WorkspaceBoundary) -> PreparedToolCall:
        request = self._arguments(call.input, workspace)
        self._require_policy(request)
        return PreparedToolCall(
            call=call,
            requires_approval=True,
            target=request.cwd,
            metadata={
                "command": request.command,
                "cwd": request.cwd,
                "relative_cwd": request.relative_cwd,
                "reason": request.reason,
                "timeout_seconds": request.timeout_seconds,
            },
        )

    async def execute(self, prepared: PreparedToolCall, context: ToolContext) -> ToolResult:
        """Run the frozen command, retaining and emitting one shared bounded output."""
        started = time.monotonic()
        request: _RunRequest | None = None
        process: asyncio.subprocess.Process | None = None
        reader: asyncio.Task[_CapturedOutput] | None = None
        try:
            request = self._prepared_request(prepared)
            cwd = context.workspace.resolve(request.cwd)
            if not cwd.is_dir() or str(cwd) != prepared.target:
                raise ToolInputError("COMMAND_CWD_CHANGED", "command cwd changed after approval")
            relative_cwd = _workspace_relative_cwd(context.workspace, cwd)
            request = _RunRequest(
                request.command,
                str(cwd),
                relative_cwd,
                request.reason,
                request.timeout_seconds,
            )
            self._require_policy(request)
            if context.cancellation.cancelled:
                return self._outcome(
                    prepared.call,
                    request,
                    _CapturedOutput(b"", 0),
                    None,
                    started,
                    cancelled=True,
                )

            with tempfile.TemporaryDirectory(prefix="coding-agent-home-") as isolated_home:
                process = await asyncio.create_subprocess_shell(
                    request.command,
                    cwd=request.cwd,
                    env=self._child_environment(isolated_home),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
                assert process.stdout is not None
                reader = asyncio.create_task(
                    self._drain_output(process.stdout, context.emit_output)
                )
                status = await self._wait_for_process(
                    process,
                    context.cancellation,
                    request.timeout_seconds,
                )
                captured = await reader
        except asyncio.CancelledError:
            if process is not None:
                await self._terminate_process_group(process)
            if reader is not None:
                with suppress(Exception):
                    await reader
            raise
        except (ToolInputError, WorkspacePathError) as error:
            return self._error(prepared.call, error.code, error.message, started)
        except OSError:
            return self._error(
                prepared.call, "COMMAND_START_FAILED", "unable to start command", started
            )

        return self._outcome(
            prepared.call,
            request,
            captured,
            process.returncode,
            started,
            timed_out=status == "timed_out",
            cancelled=status == "cancelled",
        )

    def _arguments(
        self, input_value: Mapping[str, object], workspace: WorkspaceBoundary
    ) -> _RunRequest:
        allowed = {"command", "cwd", "reason", "timeout_seconds"}
        if set(input_value) - allowed:
            raise ToolInputError("INVALID_ARGUMENT", "run_command received an unknown argument")
        command = input_value.get("command")
        if not isinstance(command, str) or not command:
            raise ToolInputError("INVALID_ARGUMENT", "command must be a non-empty string")
        cwd_value = input_value.get("cwd", ".")
        if not isinstance(cwd_value, str) or not cwd_value:
            raise ToolInputError("INVALID_ARGUMENT", "cwd must be a non-empty string")
        cwd = workspace.resolve(cwd_value)
        if not cwd.is_dir():
            raise WorkspacePathError("COMMAND_CWD_NOT_DIRECTORY", "command cwd must be a directory")
        reason = input_value.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ToolInputError("INVALID_ARGUMENT", "reason must be a string")
        timeout = input_value.get("timeout_seconds", self.default_timeout_seconds)
        if (
            (
                "timeout_seconds" in input_value
                and (isinstance(timeout, bool) or not isinstance(timeout, int))
            )
            or not isinstance(timeout, int | float)
            or timeout <= 0
        ):
            raise ToolInputError("INVALID_ARGUMENT", "timeout_seconds must be a positive integer")
        return _RunRequest(
            command,
            str(cwd),
            _workspace_relative_cwd(workspace, cwd),
            reason,
            float(timeout),
        )

    def _prepared_request(self, prepared: PreparedToolCall) -> _RunRequest:
        metadata = prepared.metadata
        command = metadata.get("command")
        cwd = metadata.get("cwd")
        relative_cwd = metadata.get("relative_cwd")
        reason = metadata.get("reason")
        timeout = metadata.get("timeout_seconds")
        if (
            not isinstance(command, str)
            or not command
            or not isinstance(cwd, str)
            or not cwd
            or not isinstance(relative_cwd, str)
            or reason is not None
            and not isinstance(reason, str)
            or isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or timeout <= 0
        ):
            raise ToolInputError("INVALID_PREPARED_CALL", "prepared command is invalid")
        return _RunRequest(command, cwd, relative_cwd, reason, float(timeout))

    def _require_policy(self, request: _RunRequest) -> None:
        if self.command_policy is not None and not self.command_policy.allows(
            request.command, Path(request.relative_cwd)
        ):
            raise ToolInputError(
                "COMMAND_NOT_ALLOWED", "command and cwd are not allowed by evaluation policy"
            )

    async def _drain_output(
        self, stream: asyncio.StreamReader, emit_output: OutputSink
    ) -> _CapturedOutput:
        retained = bytearray()
        byte_count = 0
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while chunk := await stream.read(16 * 1024):
            byte_count += len(chunk)
            remaining = self.output_limit_bytes - len(retained)
            if remaining <= 0:
                continue
            accepted = chunk[:remaining]
            retained.extend(accepted)
            text = decoder.decode(accepted, final=False)
            if text:
                await emit_output(text)
        final_text = decoder.decode(b"", final=True)
        if final_text:
            await emit_output(final_text)
        return _CapturedOutput(bytes(retained), byte_count)

    async def _wait_for_process(
        self,
        process: asyncio.subprocess.Process,
        cancellation: CancellationToken,
        timeout_seconds: float,
    ) -> str:
        process_waiter = asyncio.create_task(process.wait())
        cancellation_waiter = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {process_waiter, cancellation_waiter},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if process_waiter in done:
                await self._terminate_remaining_group(process.pid)
                return "completed"
            if cancellation_waiter in done:
                await self._terminate_process_group(process, process_waiter)
                return "cancelled"
            await self._terminate_process_group(process, process_waiter)
            return "timed_out"
        finally:
            cancellation_waiter.cancel()
            with suppress(asyncio.CancelledError):
                await cancellation_waiter

    async def _terminate_process_group(
        self,
        process: asyncio.subprocess.Process,
        process_waiter: asyncio.Task[int] | None = None,
    ) -> None:
        waiter = process_waiter or asyncio.create_task(process.wait())
        self._signal_group(process.pid, signal.SIGTERM)
        deadline = asyncio.get_running_loop().time() + self.kill_grace_seconds
        while self._group_exists(process.pid) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(min(0.01, self.kill_grace_seconds))
        if self._group_exists(process.pid):
            self._signal_group(process.pid, signal.SIGKILL)
        await waiter

    async def _terminate_remaining_group(self, process_group: int) -> None:
        if not self._group_exists(process_group):
            return
        self._signal_group(process_group, signal.SIGTERM)
        deadline = asyncio.get_running_loop().time() + self.kill_grace_seconds
        while self._group_exists(process_group) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(min(0.01, self.kill_grace_seconds))
        if self._group_exists(process_group):
            self._signal_group(process_group, signal.SIGKILL)

    @staticmethod
    def _signal_group(process_group: int, signal_number: signal.Signals) -> None:
        try:
            os.killpg(process_group, signal_number)
        except ProcessLookupError:
            pass

    @staticmethod
    def _group_exists(process_group: int) -> bool:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _child_environment(self, isolated_home: str) -> dict[str, str]:
        environ = os.environ
        child = {"PATH": environ.get("PATH", os.defpath), "HOME": isolated_home}
        for name in environ:
            if name in _LOCALE_ENV_NAMES or name in _TEMP_ENV_NAMES:
                child[name] = environ[name]
        for name in self.pass_env:
            if name in environ and name != self.model_api_key_env:
                child[name] = environ[name]
        child.pop(self.model_api_key_env, None)
        return child

    def _outcome(
        self,
        call: ToolCall,
        request: _RunRequest,
        captured: _CapturedOutput,
        exit_code: int | None,
        started: float,
        *,
        timed_out: bool = False,
        cancelled: bool = False,
    ) -> ToolResult:
        output = captured.data.decode("utf-8", errors="replace")
        data = {
            "command": request.command,
            "cwd": request.cwd,
            "output": output,
            "output_bytes": captured.byte_count,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "cancelled": cancelled,
        }
        truncated = captured.byte_count > len(captured.data)
        if timed_out:
            error = ToolError("COMMAND_TIMEOUT", "command timed out")
            summary = error.message
        elif cancelled:
            error = ToolError("COMMAND_CANCELLED", "command was cancelled")
            summary = error.message
        elif exit_code != 0:
            error = ToolError("COMMAND_FAILED", f"command exited with code {exit_code}")
            summary = error.message
        else:
            error = None
            summary = "command completed"
        return result(
            call.id,
            self.name,
            ok=error is None,
            summary=summary,
            data=data,
            error=error,
            truncated=truncated,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _error(self, call: ToolCall, code: str, message: str, started: float) -> ToolResult:
        error = ToolError(code, message)
        return result(
            call.id,
            self.name,
            ok=False,
            summary=message,
            error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _workspace_relative_cwd(workspace: WorkspaceBoundary, cwd: Path) -> str:
    return _relative_cwd_text(cwd.relative_to(workspace.root))


def _relative_cwd_text(cwd: Path) -> str:
    text = cwd.as_posix()
    return "." if text in {"", "."} else text


__all__ = ["AllowedCommand", "CommandPolicy", "RunCommandTool"]
