"""The model-visible tool schemas and local preparation dispatch."""

from __future__ import annotations

from coding_agent.core.models import PreparedToolCall, ToolCall, ToolResult
from coding_agent.tools import ToolContext, ToolInputError, error_result
from coding_agent.tools.paths import WorkspaceBoundary, WorkspacePathError
from coding_agent.tools.read_file import ReadFileTool
from coding_agent.tools.write_file import WriteFileTool


class ToolRegistry:
    """Dispatch known local calls while preserving user-correctable errors as results."""

    def __init__(
        self,
        read_file: ReadFileTool | None = None,
        write_file: WriteFileTool | None = None,
    ) -> None:
        self._read_file = read_file or ReadFileTool()
        self._write_file = write_file or WriteFileTool()

    def schemas(self) -> list[dict[str, object]]:
        return [
            {
                "name": "read_file",
                "description": "Read a UTF-8 text file from the workspace.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "offset": {"type": "integer", "minimum": 1, "default": 1},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": self._read_file.max_lines,
                            "default": self._read_file.max_lines,
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "write_file",
                "description": "Propose a workspace file write or replacement.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string", "enum": ["write", "replace"]},
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                        "replace_all": {"type": "boolean", "default": False},
                    },
                    "required": ["operation", "path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "run_command",
                "description": "Propose a non-interactive command in the workspace.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "reason": {"type": "string"},
                        "timeout_seconds": {"type": "integer", "minimum": 1, "default": 120},
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            },
        ]

    def prepare(
        self, call: ToolCall, workspace: WorkspaceBoundary
    ) -> PreparedToolCall | ToolResult:
        if call.name == ReadFileTool.name:
            tool = self._read_file
        elif call.name == WriteFileTool.name:
            tool = self._write_file
        else:
            return error_result(call.id, call.name, "UNKNOWN_TOOL", f"unknown tool: {call.name}")
        try:
            return tool.prepare(call, workspace)
        except (ToolInputError, WorkspacePathError) as error:
            return error_result(call.id, call.name, error.code, error.message)


__all__ = ["ToolContext", "ToolRegistry"]
