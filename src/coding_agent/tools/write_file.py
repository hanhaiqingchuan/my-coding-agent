"""Approved, text-only workspace writes with a frozen diff and atomic commit."""

from __future__ import annotations

import difflib
import hashlib
import os
import stat
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from coding_agent.core.models import PreparedToolCall, ToolCall, ToolError, ToolResult
from coding_agent.tools import ToolContext, ToolInputError, result
from coding_agent.tools.paths import WorkspaceBoundary, WorkspacePathError


@dataclass(frozen=True, slots=True)
class _WriteRequest:
    operation: str
    path: str
    content: str
    old_text: str | None
    new_text: str | None
    replace_all: bool


class WriteFileTool:
    """Prepare a reviewed write proposal, then commit it only if its baseline remains intact."""

    name = "write_file"

    def prepare(self, call: ToolCall, workspace: WorkspaceBoundary) -> PreparedToolCall:
        request = self._arguments(call.input)
        target = workspace.resolve(request.path, allow_missing_leaf=True)
        before, baseline_sha256 = self._read_baseline(target)
        content = self._content_for(request, before)
        relative_target = target.relative_to(workspace.root).as_posix()
        preview = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{relative_target}",
                tofile=f"b/{relative_target}",
            )
        )
        return PreparedToolCall(
            call=call,
            requires_approval=True,
            target=str(target),
            preview=preview,
            baseline_sha256=baseline_sha256,
            metadata={
                "operation": request.operation,
                "path": str(target),
                "content": content,
            },
        )

    async def execute(self, prepared: PreparedToolCall, context: ToolContext) -> ToolResult:
        """Revalidate the prepared target and atomically replace it with the frozen content."""
        started = time.monotonic()
        call = prepared.call
        try:
            context.cancellation.raise_if_cancelled()
            request = self._prepared_request(prepared)
            target = context.workspace.resolve(request.path, allow_missing_leaf=True)
            if str(target) != prepared.target or not self._baseline_matches(
                target, prepared.baseline_sha256
            ):
                return self._error(
                    call,
                    "WRITE_CONFLICT",
                    "file changed after write approval",
                    started,
                )
            context.cancellation.raise_if_cancelled()
            self._atomic_write(target, request.content)
        except WorkspacePathError as error:
            return self._error(call, error.code, error.message, started)
        except ToolInputError as error:
            return self._error(call, error.code, error.message, started)
        except OSError:
            return self._error(call, "WRITE_FAILED", "unable to write file", started)

        return result(
            call.id,
            self.name,
            ok=True,
            summary="wrote file",
            data={"path": str(target)},
            duration_ms=self._duration_ms(started),
        )

    def _arguments(self, input_value: Mapping[str, object]) -> _WriteRequest:
        allowed = {"operation", "path", "content", "old_text", "new_text", "replace_all"}
        if set(input_value) - allowed:
            raise ToolInputError("INVALID_ARGUMENT", "write_file received an unknown argument")
        operation = input_value.get("operation")
        path = input_value.get("path")
        if operation not in {"write", "replace"}:
            raise ToolInputError("INVALID_OPERATION", "operation must be write or replace")
        if not isinstance(path, str) or not path:
            raise ToolInputError("INVALID_ARGUMENT", "path must be a non-empty string")
        if operation == "write":
            content = input_value.get("content")
            if not isinstance(content, str):
                raise ToolInputError("INVALID_ARGUMENT", "write content must be a string")
            if {"old_text", "new_text", "replace_all"} & set(input_value):
                raise ToolInputError(
                    "INVALID_ARGUMENT", "write must not include replacement arguments"
                )
            return _WriteRequest("write", path, content, None, None, False)

        old_text = input_value.get("old_text")
        new_text = input_value.get("new_text")
        replace_all = input_value.get("replace_all", False)
        if "content" in input_value:
            raise ToolInputError("INVALID_ARGUMENT", "replace must not include content")
        if not isinstance(old_text, str) or not old_text:
            raise ToolInputError("EMPTY_OLD_TEXT", "old_text must be a non-empty string")
        if not isinstance(new_text, str):
            raise ToolInputError("INVALID_ARGUMENT", "new_text must be a string")
        if not isinstance(replace_all, bool):
            raise ToolInputError("INVALID_ARGUMENT", "replace_all must be a boolean")
        return _WriteRequest("replace", path, "", old_text, new_text, replace_all)

    def _prepared_request(self, prepared: PreparedToolCall) -> _WriteRequest:
        metadata = prepared.metadata
        operation = metadata.get("operation")
        path = metadata.get("path")
        content = metadata.get("content")
        if (
            operation != "write"
            and operation != "replace"
            or not isinstance(path, str)
            or not isinstance(content, str)
        ):
            raise ToolInputError("INVALID_ARGUMENT", "prepared write proposal is invalid")
        return _WriteRequest(operation, path, content, None, None, False)

    def _content_for(self, request: _WriteRequest, before: str) -> str:
        if request.operation == "write":
            return request.content
        assert request.old_text is not None
        assert request.new_text is not None
        count = before.count(request.old_text)
        if count == 0:
            raise ToolInputError("REPLACE_NOT_FOUND", "old_text was not found in the file")
        if count > 1 and not request.replace_all:
            raise ToolInputError(
                "REPLACE_NOT_UNIQUE",
                "old_text occurs more than once; set replace_all to replace all",
            )
        return before.replace(request.old_text, request.new_text, -1 if request.replace_all else 1)

    def _read_baseline(self, target: Path) -> tuple[str, str | None]:
        if not target.exists():
            return "", None
        try:
            mode = target.stat().st_mode
        except OSError as error:
            raise ToolInputError(
                "WRITE_PREPARE_FAILED", "unable to read file for write preview"
            ) from error
        if not stat.S_ISREG(mode):
            raise ToolInputError("NOT_REGULAR_FILE", "path is not a regular file")
        try:
            raw = target.read_bytes()
        except OSError as error:
            raise ToolInputError(
                "WRITE_PREPARE_FAILED", "unable to read file for write preview"
            ) from error
        if b"\0" in raw:
            raise ToolInputError("BINARY_FILE", "file contains binary data")
        try:
            before = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ToolInputError("INVALID_UTF8", "file is not valid UTF-8 text") from error
        return before, hashlib.sha256(raw).hexdigest()

    def _baseline_matches(self, target: Path, baseline_sha256: str | None) -> bool:
        if baseline_sha256 is None:
            return not target.exists()
        try:
            if not target.exists() or not stat.S_ISREG(target.stat().st_mode):
                return False
            return hashlib.sha256(target.read_bytes()).hexdigest() == baseline_sha256
        except OSError:
            return False

    def _atomic_write(self, target: Path, content: str) -> None:
        mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            if mode is not None:
                os.fchmod(file_descriptor, mode)
            with os.fdopen(file_descriptor, "wb") as output:
                output.write(content.encode("utf-8"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            self._fsync_parent(target.parent)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _fsync_parent(parent: Path) -> None:
        if os.name != "posix":
            return
        try:
            descriptor = os.open(parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def _error(self, call: ToolCall, code: str, message: str, started: float) -> ToolResult:
        return result(
            call.id,
            self.name,
            ok=False,
            summary=message,
            error=ToolError(code, message),
            duration_ms=self._duration_ms(started),
        )

    @staticmethod
    def _duration_ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)
