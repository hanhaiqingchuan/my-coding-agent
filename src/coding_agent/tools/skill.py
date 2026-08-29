"""The read-only, workspace-bounded ``skill`` tool (spec section 10.5)."""

from __future__ import annotations

import stat
import time
from collections.abc import Mapping, Sequence

from coding_agent.core.models import PreparedToolCall, ToolCall, ToolError, ToolResult
from coding_agent.tools import ToolContext, ToolInputError, error_result, result
from coding_agent.tools.paths import WorkspaceBoundary, WorkspacePathError
from coding_agent.workspace_context import SKILL_MD_FILENAME, SkillInfo, parse_frontmatter


class SkillTool:
    """Serve the run's discovered skill index and full SKILL.md bodies on demand.

    The discovered set is injected per run (``configure``), mirroring how the
    composition boundary injects the command policy. Skills never require
    approval: reading them is a workspace-bounded read like ``read_file``.
    """

    name = "skill"

    def __init__(self) -> None:
        self._skills: dict[str, SkillInfo] = {}

    def configure(self, skills: Sequence[SkillInfo]) -> None:
        self._skills = {skill.name: skill for skill in skills}

    @property
    def skills(self) -> tuple[SkillInfo, ...]:
        return tuple(sorted(self._skills.values(), key=lambda skill: skill.name))

    def prepare(
        self, call: ToolCall, workspace: WorkspaceBoundary
    ) -> PreparedToolCall | ToolResult:
        mode, name = self._arguments(call.input)
        if mode == "list":
            return PreparedToolCall(
                call=call,
                requires_approval=False,
                target=str(workspace.root),
                metadata={"mode": "list"},
            )
        skill = self._skills.get(name) if name is not None else None
        if skill is None:
            return error_result(call.id, self.name, "UNKNOWN_SKILL", f"unknown skill: {name}")
        relative_path = f"{skill.directory}/{SKILL_MD_FILENAME}"
        try:
            target = workspace.resolve(relative_path)
        except WorkspacePathError as error:
            return error_result(call.id, self.name, error.code, error.message)
        return PreparedToolCall(
            call=call,
            requires_approval=False,
            target=str(target),
            metadata={"mode": "read", "name": skill.name, "path": relative_path},
        )

    async def execute(self, prepared: PreparedToolCall, context: ToolContext) -> ToolResult:
        """Re-resolve the target immediately before I/O and return a tool envelope."""
        started = time.monotonic()
        call = prepared.call
        if prepared.metadata.get("mode") == "list":
            skills = self.skills
            return result(
                call.id,
                self.name,
                ok=True,
                summary=f"{len(skills)} skill(s) available",
                data={
                    "skills": [
                        {
                            "name": skill.name,
                            "description": skill.description,
                            "files": skill.file_count,
                        }
                        for skill in skills
                    ]
                },
                duration_ms=self._duration_ms(started),
            )
        name = prepared.metadata.get("name")
        skill = self._skills.get(name) if isinstance(name, str) else None
        if skill is None:
            return self._error(call, "UNKNOWN_SKILL", f"unknown skill: {name}", started)
        try:
            context.cancellation.raise_if_cancelled()
            target = context.workspace.resolve(f"{skill.directory}/{SKILL_MD_FILENAME}")
            if not stat.S_ISREG(target.stat().st_mode):
                return self._error(call, "NOT_REGULAR_FILE", "path is not a regular file", started)
            raw = target.read_bytes()
            context.cancellation.raise_if_cancelled()
        except WorkspacePathError as error:
            return self._error(call, error.code, error.message, started)
        except OSError:
            return self._error(call, "READ_FAILED", "unable to read skill file", started)

        if b"\0" in raw:
            return self._error(call, "BINARY_FILE", "file contains binary data", started)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return self._error(call, "INVALID_UTF8", "file is not valid UTF-8 text", started)
        parsed = parse_frontmatter(text)
        if parsed is None:
            return self._error(
                call, "INVALID_FRONTMATTER", "SKILL.md has invalid frontmatter", started
            )
        _fields, body = parsed
        return result(
            call.id,
            self.name,
            ok=True,
            summary=f"loaded skill {skill.name}",
            data={
                "name": skill.name,
                "description": skill.description,
                "body": body,
                "files": self._companion_files(context.workspace, skill),
            },
            duration_ms=self._duration_ms(started),
        )

    def _arguments(self, input_value: Mapping[str, object]) -> tuple[str, str | None]:
        allowed = {"name", "mode"}
        if set(input_value) - allowed:
            raise ToolInputError("INVALID_ARGUMENT", "skill received an unknown argument")
        mode = input_value.get("mode", "read")
        if mode not in {"read", "list"}:
            raise ToolInputError("INVALID_ARGUMENT", "mode must be read or list")
        name = input_value.get("name")
        if name is not None and not isinstance(name, str):
            raise ToolInputError("INVALID_ARGUMENT", "name must be a string")
        if mode == "read" and (not isinstance(name, str) or not name):
            raise ToolInputError("INVALID_ARGUMENT", "name must be a non-empty string")
        return mode, name

    def _companion_files(
        self, workspace: WorkspaceBoundary, skill: SkillInfo
    ) -> list[dict[str, object]]:
        """List the skill's bundled files, one level deep, with their sizes."""
        files: list[dict[str, object]] = []
        try:
            entries = sorted(
                (workspace.root / skill.directory).iterdir(), key=lambda item: item.name
            )
        except OSError:
            return files
        for entry in entries:
            if not entry.is_file() or entry.name == SKILL_MD_FILENAME:
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            files.append({"path": f"{skill.directory}/{entry.name}", "bytes": size})
        return files

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


__all__ = ["SkillTool"]
