from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.models import PreparedToolCall, ToolCall
from coding_agent.tools import ToolContext
from coding_agent.tools.paths import WorkspaceBoundary
from coding_agent.tools.read_file import ReadFileTool
from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.skill import SkillTool
from coding_agent.workspace_context import SkillInfo

_GIT_HELPER = SkillInfo(
    name="git-helper",
    description="Guide routine Git operations.",
    directory=".agents/skills/git-helper",
    file_count=3,
)


def _context(workspace: WorkspaceBoundary) -> ToolContext:
    async def sink(_: str) -> None:
        return None

    return ToolContext(workspace=workspace, cancellation=CancellationToken(), emit_output=sink)


def _git_helper_workspace(tmp_path: Path) -> WorkspaceBoundary:
    skill_dir = tmp_path / ".agents" / "skills" / "git-helper"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: git-helper\ndescription: Guide routine Git operations.\n---\n\n"
        "1. Inspect the current branch.\n2. Propose the next command.\n",
        encoding="utf-8",
    )
    (skill_dir / "refs.md").write_text("reference notes\n", encoding="utf-8")
    (skill_dir / "run.sh").write_text("#!/bin/sh\necho run\n", encoding="utf-8")
    return WorkspaceBoundary(tmp_path)


def _configured_tool(skills: tuple[SkillInfo, ...]) -> SkillTool:
    tool = SkillTool()
    tool.configure(skills)
    return tool


def test_skill_prepare_never_requires_approval(tmp_path: Path) -> None:
    workspace = _git_helper_workspace(tmp_path)
    tool = _configured_tool((_GIT_HELPER,))

    read = tool.prepare(ToolCall("call-read", "skill", {"name": "git-helper"}), workspace)
    listing = tool.prepare(ToolCall("call-list", "skill", {"mode": "list"}), workspace)

    assert isinstance(read, PreparedToolCall)
    assert read.requires_approval is False
    assert isinstance(listing, PreparedToolCall)
    assert listing.requires_approval is False


@pytest.mark.asyncio
async def test_skill_list_returns_the_discovered_index(tmp_path: Path) -> None:
    tool = _configured_tool((_GIT_HELPER,))
    workspace = WorkspaceBoundary(tmp_path)
    prepared = tool.prepare(ToolCall("call-list", "skill", {"mode": "list"}), workspace)
    assert isinstance(prepared, PreparedToolCall)

    result = await tool.execute(prepared, _context(workspace))

    assert result.ok is True
    assert result.error is None
    assert json.loads(result.content)["data"] == {
        "skills": [
            {"name": "git-helper", "description": "Guide routine Git operations.", "files": 3}
        ]
    }


@pytest.mark.asyncio
async def test_skill_list_without_discovered_skills_is_an_empty_success(tmp_path: Path) -> None:
    tool = SkillTool()
    workspace = WorkspaceBoundary(tmp_path)
    prepared = tool.prepare(ToolCall("call-list", "skill", {"mode": "list"}), workspace)
    assert isinstance(prepared, PreparedToolCall)

    result = await tool.execute(prepared, _context(workspace))

    assert result.ok is True
    assert json.loads(result.content)["data"] == {"skills": []}


@pytest.mark.asyncio
async def test_skill_read_returns_the_body_and_companion_listing(tmp_path: Path) -> None:
    workspace = _git_helper_workspace(tmp_path)
    tool = _configured_tool((_GIT_HELPER,))
    prepared = tool.prepare(ToolCall("call-read", "skill", {"name": "git-helper"}), workspace)
    assert isinstance(prepared, PreparedToolCall)

    result = await tool.execute(prepared, _context(workspace))

    assert result.ok is True
    data = json.loads(result.content)["data"]
    assert data["name"] == "git-helper"
    assert data["description"] == "Guide routine Git operations."
    assert data["body"].startswith("\n1. Inspect the current branch.")
    assert "name: git-helper" not in data["body"]
    assert data["files"] == [
        {"path": ".agents/skills/git-helper/refs.md", "bytes": 16},
        {"path": ".agents/skills/git-helper/run.sh", "bytes": 19},
    ]


def test_skill_read_with_an_unknown_name_returns_unknown_skill(tmp_path: Path) -> None:
    workspace = WorkspaceBoundary(tmp_path)
    tool = _configured_tool((_GIT_HELPER,))

    result = tool.prepare(ToolCall("call-read", "skill", {"name": "nope"}), workspace)

    assert not isinstance(result, PreparedToolCall)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "UNKNOWN_SKILL"
    assert "nope" in result.error.message


@pytest.mark.parametrize(
    "arguments",
    [
        {"name": "git-helper", "unexpected": True},
        {"name": "git-helper", "mode": "fetch"},
        {"mode": "read"},
        {"mode": "read", "name": ""},
        {"mode": "read", "name": 7},
        {"mode": "list", "name": 7},
    ],
)
def test_skill_rejects_invalid_arguments_as_correctable_errors(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    workspace = _git_helper_workspace(tmp_path)
    registry = ToolRegistry()
    registry.configure_skills((_GIT_HELPER,))

    result = registry.prepare(ToolCall("call-invalid", "skill", arguments), workspace)

    assert not isinstance(result, PreparedToolCall)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "INVALID_ARGUMENT"


def test_skill_read_resolves_through_the_workspace_boundary(tmp_path: Path) -> None:
    workspace = _git_helper_workspace(tmp_path)
    outside = tmp_path.parent / "outside-skill.md"
    outside.write_text("---\nname: git-helper\ndescription: evil\n---\noutside\n", encoding="utf-8")
    skill_md = tmp_path / ".agents" / "skills" / "git-helper" / "SKILL.md"
    skill_md.unlink()
    skill_md.symlink_to(outside)
    tool = _configured_tool((_GIT_HELPER,))

    prepared = tool.prepare(ToolCall("call-read", "skill", {"name": "git-helper"}), workspace)

    assert not isinstance(prepared, PreparedToolCall)
    assert prepared.error is not None
    assert prepared.error.code in {"PATH_OUTSIDE_WORKSPACE", "PATH_RESOLUTION_FAILED"}


@pytest.mark.asyncio
async def test_registry_dispatches_the_skill_tool_without_approval(tmp_path: Path) -> None:
    workspace = _git_helper_workspace(tmp_path)
    registry = ToolRegistry()
    registry.configure_skills((_GIT_HELPER,))

    prepared = registry.prepare(ToolCall("call-read", "skill", {"name": "git-helper"}), workspace)

    assert isinstance(prepared, PreparedToolCall)
    assert prepared.requires_approval is False
    result = await registry.execute(prepared, _context(workspace))
    assert result.ok is True
    assert json.loads(result.content)["data"]["name"] == "git-helper"


def test_registry_exposes_the_skill_schema_with_closed_properties() -> None:
    schema = next(item for item in ToolRegistry().schemas() if item["name"] == "skill")

    assert schema["description"]
    input_schema = schema["input_schema"]
    assert isinstance(input_schema, dict)
    assert input_schema["additionalProperties"] is False
    assert input_schema["properties"]["mode"] == {
        "type": "string",
        "enum": ["read", "list"],
        "default": "read",
    }
    assert input_schema["properties"]["name"] == {"type": "string"}


@pytest.mark.asyncio
async def test_companion_files_are_readable_via_read_file_inside_the_boundary(
    tmp_path: Path,
) -> None:
    workspace = _git_helper_workspace(tmp_path)
    read_file = ReadFileTool()

    prepared = read_file.prepare(
        ToolCall("call-read", "read_file", {"path": ".agents/skills/git-helper/refs.md"}),
        workspace,
    )
    result = await read_file.execute(prepared, _context(workspace))

    assert result.ok is True
    assert result.data["content"] == "reference notes\n"
