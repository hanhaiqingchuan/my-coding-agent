from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.config import ToolSettings
from coding_agent.core.models import PreparedToolCall, ToolCall
from coding_agent.tools.paths import WorkspaceBoundary
from coding_agent.tools.read_file import ReadFileTool
from coding_agent.tools.registry import ToolRegistry


def test_registry_exposes_closed_schemas_for_all_model_visible_tools() -> None:
    """Permissive schemas would allow the model to send unvalidated tool arguments."""
    schemas = ToolRegistry().schemas()

    assert {schema["name"] for schema in schemas} == {"read_file", "write_file", "run_command"}
    for schema in schemas:
        input_schema = schema["input_schema"]
        assert isinstance(input_schema, dict)
        assert input_schema["additionalProperties"] is False


def test_read_schema_uses_the_configured_line_limit() -> None:
    """A stale schema maximum would let the model request calls the tool rejects."""
    registry = ToolRegistry(ReadFileTool(ToolSettings(read_max_lines=12)))

    schema = next(schema for schema in registry.schemas() if schema["name"] == "read_file")
    input_schema = schema["input_schema"]
    assert isinstance(input_schema, dict)
    properties = input_schema["properties"]
    assert isinstance(properties, dict)
    limit = properties["limit"]
    assert isinstance(limit, dict)
    assert limit["maximum"] == 12


def test_registry_prepares_read_without_approval(tmp_path: Path) -> None:
    """Marking reads as approval-gated would stall the normal agent tool loop."""
    (tmp_path / "notes.txt").write_text("notes", encoding="utf-8")

    prepared = ToolRegistry().prepare(
        ToolCall("call-read", "read_file", {"path": "notes.txt"}), WorkspaceBoundary(tmp_path)
    )

    assert isinstance(prepared, PreparedToolCall)
    assert prepared.requires_approval is False
    assert prepared.target == str((tmp_path / "notes.txt").resolve())


@pytest.mark.parametrize(
    "call",
    [
        ToolCall("call-unknown", "list_files", {}),
        ToolCall("call-invalid", "read_file", {"path": "../secret.txt"}),
    ],
)
def test_registry_returns_tool_result_for_unknown_or_invalid_calls(
    tmp_path: Path, call: ToolCall
) -> None:
    """Escalating model-correctable calls would terminate an otherwise recoverable run."""
    result = ToolRegistry().prepare(call, WorkspaceBoundary(tmp_path))

    assert not isinstance(result, PreparedToolCall)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code in {
        "UNKNOWN_TOOL",
        "PATH_OUTSIDE_WORKSPACE",
        "PATH_PARENT_TRAVERSAL",
    }


def test_registry_returns_tool_result_when_path_resolution_hits_a_symlink_cycle(
    tmp_path: Path,
) -> None:
    """A link loop must remain a recoverable model-visible path failure."""
    loop = tmp_path / "loop"
    loop.symlink_to(loop)

    result = ToolRegistry().prepare(
        ToolCall("call-loop", "read_file", {"path": "loop"}), WorkspaceBoundary(tmp_path)
    )

    assert not isinstance(result, PreparedToolCall)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "PATH_RESOLUTION_FAILED"
