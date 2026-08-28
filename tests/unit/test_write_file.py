from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.models import PreparedToolCall, ToolCall
from coding_agent.tools import ToolContext
from coding_agent.tools.paths import WorkspaceBoundary
from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.write_file import WriteFileTool


async def _discard_output(_: str) -> None:
    return None


def tool_context(workspace: Path) -> ToolContext:
    return ToolContext(WorkspaceBoundary(workspace), CancellationToken(), _discard_output)


async def execute_write(workspace: Path, input_value: dict[str, object]):
    tool = WriteFileTool()
    prepared = tool.prepare(
        ToolCall("write-1", "write_file", input_value), WorkspaceBoundary(workspace)
    )
    return prepared, await tool.execute(prepared, tool_context(workspace))


@pytest.mark.asyncio
async def test_write_creates_file_and_freezes_preview_for_approval(tmp_path: Path) -> None:
    """Dropping the preview or approval gate would hide a file creation from the user."""
    prepared, result = await execute_write(
        tmp_path, {"operation": "write", "path": "notes.txt", "content": "hello\n"}
    )

    assert prepared.requires_approval is True
    assert prepared.target == str((tmp_path / "notes.txt").resolve())
    assert prepared.baseline_sha256 is None
    assert prepared.preview == "--- a/notes.txt\n+++ b/notes.txt\n@@ -0,0 +1 @@\n+hello\n"
    assert result.ok is True
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello\n"


@pytest.mark.asyncio
async def test_write_accepts_empty_content(tmp_path: Path) -> None:
    """Treating an empty string as absent would make it impossible to create empty files."""
    _, result = await execute_write(
        tmp_path, {"operation": "write", "path": "empty.txt", "content": ""}
    )

    assert result.ok is True
    assert (tmp_path / "empty.txt").read_text(encoding="utf-8") == ""


@pytest.mark.asyncio
async def test_write_replaces_the_entire_existing_file(tmp_path: Path) -> None:
    """Using a patch-like operation for write would leave obsolete text behind."""
    target = tmp_path / "notes.txt"
    target.write_text("old\n", encoding="utf-8")

    prepared, result = await execute_write(
        tmp_path, {"operation": "write", "path": "notes.txt", "content": "new\n"}
    )

    assert "-old\n+new\n" in prepared.preview
    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_replace_updates_a_unique_match(tmp_path: Path) -> None:
    """A replace that writes the wrong occurrence changes user code unexpectedly."""
    target = tmp_path / "notes.txt"
    target.write_text("first\ntarget\nlast\n", encoding="utf-8")

    _, result = await execute_write(
        tmp_path,
        {
            "operation": "replace",
            "path": "notes.txt",
            "old_text": "target",
            "new_text": "changed",
        },
    )

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "first\nchanged\nlast\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("contents", "input_value", "code"),
    [
        (
            "one\n",
            {
                "operation": "replace",
                "path": "notes.txt",
                "old_text": "missing",
                "new_text": "changed",
            },
            "REPLACE_NOT_FOUND",
        ),
        (
            "target\ntarget\n",
            {
                "operation": "replace",
                "path": "notes.txt",
                "old_text": "target",
                "new_text": "changed",
            },
            "REPLACE_NOT_UNIQUE",
        ),
        (
            "target\n",
            {
                "operation": "replace",
                "path": "notes.txt",
                "old_text": "",
                "new_text": "changed",
            },
            "EMPTY_OLD_TEXT",
        ),
    ],
)
async def test_replace_rejects_ambiguous_or_empty_search_text(
    tmp_path: Path, contents: str, input_value: dict[str, object], code: str
) -> None:
    """Relaxing replace validation can silently rewrite every position in a file."""
    (tmp_path / "notes.txt").write_text(contents, encoding="utf-8")

    result = ToolRegistry().prepare(
        ToolCall("write-invalid", "write_file", input_value), WorkspaceBoundary(tmp_path)
    )

    assert not isinstance(result, PreparedToolCall)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == code


@pytest.mark.asyncio
async def test_replace_all_updates_every_match_only_when_requested(tmp_path: Path) -> None:
    """Ignoring replace_all would leave a proposal's visible diff inconsistent with execution."""
    target = tmp_path / "notes.txt"
    target.write_text("target\ntarget\n", encoding="utf-8")

    _, result = await execute_write(
        tmp_path,
        {
            "operation": "replace",
            "path": "notes.txt",
            "old_text": "target",
            "new_text": "changed",
            "replace_all": True,
        },
    )

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "changed\nchanged\n"


@pytest.mark.asyncio
async def test_write_rejects_missing_parent_without_creating_it(tmp_path: Path) -> None:
    """Auto-creating parents would expand an approved target beyond its proposal."""
    result = ToolRegistry().prepare(
        ToolCall(
            "write-parent",
            "write_file",
            {"operation": "write", "path": "missing/notes.txt", "content": "text"},
        ),
        WorkspaceBoundary(tmp_path),
    )

    assert not isinstance(result, PreparedToolCall)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "PATH_PARENT_NOT_FOUND"
    assert not (tmp_path / "missing").exists()


def test_write_rejects_an_existing_directory(tmp_path: Path) -> None:
    """Treating a directory as a writable file would fail after approval instead of before it."""
    (tmp_path / "directory").mkdir()

    result = ToolRegistry().prepare(
        ToolCall(
            "write-directory",
            "write_file",
            {"operation": "write", "path": "directory", "content": "text"},
        ),
        WorkspaceBoundary(tmp_path),
    )

    assert not isinstance(result, PreparedToolCall)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "NOT_REGULAR_FILE"


@pytest.mark.parametrize("path", ["../outside.txt", "escape.txt"])
def test_write_path_errors_stay_inside_workspace(tmp_path: Path, path: str) -> None:
    """Bypassing the read tool's boundary would let writes escape the session workspace."""
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    if path == "escape.txt":
        (tmp_path / path).symlink_to(outside)

    result = ToolRegistry().prepare(
        ToolCall("write-path", "write_file", {"operation": "write", "path": path, "content": "x"}),
        WorkspaceBoundary(tmp_path),
    )

    assert not isinstance(result, PreparedToolCall)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code in {"PATH_PARENT_TRAVERSAL", "PATH_OUTSIDE_WORKSPACE"}
    assert outside.read_text(encoding="utf-8") == "private"


@pytest.mark.asyncio
async def test_write_rejects_changed_baseline(tmp_path: Path) -> None:
    """Without a baseline check, approval can overwrite a later external edit."""
    target = tmp_path / "notes.txt"
    target.write_text("before", encoding="utf-8")
    tool = WriteFileTool()
    prepared = tool.prepare(
        ToolCall(
            "write-conflict",
            "write_file",
            {"operation": "write", "path": "notes.txt", "content": "after"},
        ),
        WorkspaceBoundary(tmp_path),
    )
    target.write_text("external change", encoding="utf-8")

    result = await tool.execute(prepared, tool_context(tmp_path))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "WRITE_CONFLICT"
    assert target.read_text(encoding="utf-8") == "external change"


@pytest.mark.asyncio
async def test_atomic_replace_failure_keeps_existing_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing the destination in place would corrupt it if the final commit fails."""
    target = tmp_path / "notes.txt"
    target.write_text("before", encoding="utf-8")
    tool = WriteFileTool()
    prepared = tool.prepare(
        ToolCall(
            "write-atomic",
            "write_file",
            {"operation": "write", "path": "notes.txt", "content": "after"},
        ),
        WorkspaceBoundary(tmp_path),
    )

    def fail_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("coding_agent.tools.write_file.os.replace", fail_replace)
    result = await tool.execute(prepared, tool_context(tmp_path))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "WRITE_FAILED"
    assert target.read_text(encoding="utf-8") == "before"


@pytest.mark.asyncio
async def test_overwrite_preserves_existing_file_permissions(tmp_path: Path) -> None:
    """Replacing without copying the mode could make an executable source file unusable."""
    target = tmp_path / "script.sh"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o740)

    _, result = await execute_write(
        tmp_path, {"operation": "write", "path": "script.sh", "content": "new"}
    )

    assert result.ok is True
    assert stat.S_IMODE(target.stat().st_mode) == 0o740
