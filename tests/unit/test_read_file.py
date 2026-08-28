from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from coding_agent.config import ToolSettings
from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.models import ToolCall
from coding_agent.tools.paths import WorkspaceBoundary
from coding_agent.tools.read_file import ReadFileTool
from coding_agent.tools.registry import ToolContext, ToolRegistry


async def _discard_output(_: str) -> None:
    return None


async def execute_read(
    workspace: Path,
    input_value: dict[str, object],
    *,
    settings: ToolSettings = ToolSettings(),
):
    tool = ReadFileTool(settings)
    prepared = ToolRegistry(tool).prepare(
        ToolCall("read-1", "read_file", input_value), WorkspaceBoundary(workspace)
    )
    if not hasattr(prepared, "call"):
        return prepared
    return await tool.execute(
        prepared,
        ToolContext(WorkspaceBoundary(workspace), CancellationToken(), _discard_output),
    )


@pytest.mark.asyncio
async def test_read_file_stops_at_first_byte_limit(tmp_path: Path) -> None:
    """Dropping the byte gate could send arbitrarily large tool output to the model."""
    target = tmp_path / "large.txt"
    target.write_text("x" * 100 + "\n", encoding="utf-8")
    with target.open("a", encoding="utf-8") as output:
        for _ in range(899):
            output.write("x" * 100 + "\n")

    result = await execute_read(tmp_path, {"path": "large.txt"})

    assert result.ok is True
    assert result.truncated is True
    assert result.data["start_line"] == 1
    assert result.data["next_offset"] is not None
    assert len(str(result.data["content"]).encode("utf-8")) <= 40960
    assert int(result.data["end_line"]) < 800


@pytest.mark.asyncio
async def test_read_file_uses_one_based_offset_and_preserves_original_text(tmp_path: Path) -> None:
    """An off-by-one or newline-normalizing reader would make a later replace unreliable."""
    (tmp_path / "notes.txt").write_bytes(b"one\r\ntwo\nthree\n")

    result = await execute_read(tmp_path, {"path": "notes.txt", "offset": 2, "limit": 1})

    assert result.ok is True
    assert result.data == {
        "content": "two\n",
        "start_line": 2,
        "end_line": 2,
        "total_lines": 3,
        "next_offset": 3,
    }
    assert result.truncated is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_value", "code"),
    [
        ({"path": "text.txt", "offset": 0}, "INVALID_OFFSET"),
        ({"path": "text.txt", "offset": 4}, "OFFSET_OUT_OF_RANGE"),
        ({"path": "text.txt", "limit": 801}, "INVALID_LIMIT"),
        ({"path": "text.txt", "unknown": True}, "INVALID_ARGUMENT"),
    ],
)
async def test_read_file_returns_structured_errors_for_invalid_arguments(
    tmp_path: Path, input_value: dict[str, object], code: str
) -> None:
    """Argument mistakes must be recoverable tool results rather than run-level errors."""
    (tmp_path / "text.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = await execute_read(tmp_path, input_value)

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["directory", "fifo", "socket"])
async def test_read_file_rejects_non_regular_files(tmp_path: Path, kind: str) -> None:
    """Opening special files could block the agent or expose device data."""
    target = tmp_path / kind
    unix_socket: socket.socket | None = None
    if kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            unix_socket.bind(str(target))
        except OSError as error:
            unix_socket.close()
            pytest.skip(f"platform Unix-socket path limit: {error}")

    try:
        result = await execute_read(tmp_path, {"path": kind})
    finally:
        if unix_socket is not None:
            unix_socket.close()

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "NOT_REGULAR_FILE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("contents", "code"),
    [(b"plain\x00text", "BINARY_FILE"), (b"\xff\xfe", "INVALID_UTF8")],
)
async def test_read_file_rejects_binary_and_invalid_utf8(
    tmp_path: Path, contents: bytes, code: str
) -> None:
    """Returning undecodable bytes would violate the text-only tool contract."""
    (tmp_path / "invalid.bin").write_bytes(contents)

    result = await execute_read(tmp_path, {"path": "invalid.bin"})

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == code
