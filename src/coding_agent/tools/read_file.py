"""The bounded, text-only ``read_file`` local tool."""

from __future__ import annotations

import stat
import time
from collections.abc import Mapping

from coding_agent.config import ToolSettings
from coding_agent.core.models import PreparedToolCall, ToolCall, ToolError, ToolResult
from coding_agent.tools import ToolContext, ToolInputError, result
from coding_agent.tools.paths import WorkspaceBoundary, WorkspacePathError


class ReadFileTool:
    """Read a UTF-8 regular file with line and byte output gates."""

    name = "read_file"

    def __init__(self, settings: ToolSettings | None = None) -> None:
        self._settings = settings or ToolSettings()

    @property
    def max_lines(self) -> int:
        """Return the configured maximum exposed through the model schema."""
        return self._settings.read_max_lines

    def prepare(self, call: ToolCall, workspace: WorkspaceBoundary) -> PreparedToolCall:
        path, offset, limit = self._arguments(call.input)
        target = workspace.resolve(path)
        return PreparedToolCall(
            call=call,
            requires_approval=False,
            target=str(target),
            metadata={"path": path, "offset": offset, "limit": limit},
        )

    async def execute(self, prepared: PreparedToolCall, context: ToolContext) -> ToolResult:
        """Re-resolve the target immediately before I/O and return a tool envelope."""
        started = time.monotonic()
        call = prepared.call
        try:
            path, offset, limit = self._arguments(call.input)
            context.cancellation.raise_if_cancelled()
            target = context.workspace.resolve(path)
            if not stat.S_ISREG(target.stat().st_mode):
                return self._error(call, "NOT_REGULAR_FILE", "path is not a regular file", started)
            raw = target.read_bytes()
            context.cancellation.raise_if_cancelled()
        except WorkspacePathError as error:
            return self._error(call, error.code, error.message, started)
        except ToolInputError as error:
            return self._error(call, error.code, error.message, started)
        except OSError:
            return self._error(call, "READ_FAILED", "unable to read file", started)

        if b"\0" in raw:
            return self._error(call, "BINARY_FILE", "file contains binary data", started)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return self._error(call, "INVALID_UTF8", "file is not valid UTF-8 text", started)

        lines = text.splitlines(keepends=True)
        total_lines = len(lines)
        if offset > total_lines and (total_lines > 0 or offset != 1):
            return self._error(
                call, "OFFSET_OUT_OF_RANGE", "offset is beyond the end of the file", started
            )

        selected: list[str] = []
        used_bytes = 0
        next_offset: int | None = None
        for line_number, line in enumerate(lines[offset - 1 :], start=offset):
            if len(selected) == limit:
                next_offset = line_number
                break
            encoded_length = len(line.encode("utf-8"))
            if used_bytes + encoded_length > self._settings.read_max_bytes:
                next_offset = line_number
                break
            selected.append(line)
            used_bytes += encoded_length

        content = "".join(selected)
        end_line = offset + len(selected) - 1
        truncated = next_offset is not None
        data = {
            "content": content,
            "start_line": offset,
            "end_line": end_line,
            "total_lines": total_lines,
            "next_offset": next_offset,
        }
        return result(
            call.id,
            self.name,
            ok=True,
            summary=f"read {len(selected)} line(s)",
            data=data,
            truncated=truncated,
            duration_ms=self._duration_ms(started),
        )

    def _arguments(self, input_value: Mapping[str, object]) -> tuple[str, int, int]:
        allowed = {"path", "offset", "limit"}
        if set(input_value) - allowed:
            raise ToolInputError("INVALID_ARGUMENT", "read_file received an unknown argument")
        path = input_value.get("path")
        if not isinstance(path, str) or not path:
            raise ToolInputError("INVALID_ARGUMENT", "path must be a non-empty string")
        offset = input_value.get("offset", 1)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
            raise ToolInputError("INVALID_OFFSET", "offset must be a positive integer")
        limit = input_value.get("limit", self._settings.read_max_lines)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= self._settings.read_max_lines
        ):
            raise ToolInputError(
                "INVALID_LIMIT", f"limit must be between 1 and {self._settings.read_max_lines}"
            )
        return path, offset, limit

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
