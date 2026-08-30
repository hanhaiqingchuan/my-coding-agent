"""Run-start workspace scan: AGENTS.md instructions and on-demand skills.

Spec 7.2 (amended) makes the workspace ``AGENTS.md`` part of the mandatory system
content, and section 10.5 keeps skills out of the context until the model asks for
them: the scan reads instructions once per run and produces a terse skill index.
Both ride inside the compiled system string the context estimator measures, so
neither can be pruned or summarized away. File reads go through the same
:class:`~coding_agent.tools.paths.WorkspaceBoundary` as every other tool read.
"""

from __future__ import annotations

import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from coding_agent.tools.paths import WorkspaceBoundary, WorkspacePathError

AGENTS_MD_MAX_BYTES = 16 * 1024
AGENTS_MD_FILENAMES = ("AGENTS.md", "agents.md")
AGENTS_MD_TRUNCATION_MARKER = "[AGENTS.md truncated: showing the first {limit} of {total} bytes]"

SKILLS_DIRECTORY = ".agents/skills"
SKILL_MD_FILENAME = "SKILL.md"
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_DESCRIPTION_MAX_CHARS = 1_024

INSTRUCTIONS_HEADER = "## Workspace instructions (AGENTS.md)"
SKILLS_HEADER = "## Available skills"
SKILL_LOAD_HINT = (
    'Load a skill with the `skill` tool: mode "read" plus its name returns the full '
    'SKILL.md body and its companion file listing; mode "list" returns this index.'
)

SKILL_DIAGNOSTIC_EVENT_TYPE = "skill.invalid"

_MISSING = object()


@dataclass(frozen=True, slots=True)
class SkillInfo:
    """One discovered skill: enough to index it and locate its directory."""

    name: str
    description: str
    directory: str
    file_count: int


@dataclass(frozen=True, slots=True)
class SkillDiagnostic:
    """Why one candidate skill directory was skipped; diagnostics never fail a run."""

    directory: str
    code: str
    message: str

    def payload(self) -> dict[str, str]:
        return {"skill": self.directory, "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class WorkspaceScan:
    """The frozen run-start view of workspace instructions and skills."""

    instructions: str | None
    skills: tuple[SkillInfo, ...]
    diagnostics: tuple[SkillDiagnostic, ...]
    instructions_path: str | None = None
    """Workspace-relative file the instructions came from; ``None`` when none loaded."""


def load_workspace_instructions(boundary: WorkspaceBoundary) -> str | None:
    """Read the workspace AGENTS.md through the path boundary, capped at 16 KiB.

    A missing, unreadable, non-regular or symlink-escaping file is simply absent:
    instructions are optional, so a workspace without them must never fail a run.
    """
    loaded = _load_workspace_instructions(boundary)
    return loaded[1] if loaded is not None else None


def _load_workspace_instructions(
    boundary: WorkspaceBoundary,
) -> tuple[str, str] | None:
    """Return ``(workspace-relative path, content)`` for the loaded instructions."""
    for filename in AGENTS_MD_FILENAMES:
        if not _directory_lists_name(boundary.root, filename):
            # The lookup is case-sensitive even on case-insensitive filesystems: a
            # file stored as ``Agents.md`` must not satisfy a request for ``AGENTS.md``.
            continue
        try:
            target = boundary.resolve(filename)
            if not stat.S_ISREG(target.stat().st_mode):
                continue
            raw = target.read_bytes()
        except (WorkspacePathError, OSError):
            continue
        if b"\0" in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if len(raw) <= AGENTS_MD_MAX_BYTES:
            if not text.strip():
                return None
            return filename, text
        truncated = raw[:AGENTS_MD_MAX_BYTES].decode("utf-8", errors="ignore").rstrip()
        marker = AGENTS_MD_TRUNCATION_MARKER.format(limit=AGENTS_MD_MAX_BYTES, total=len(raw))
        return filename, f"{truncated}\n\n{marker}"
    return None


def parse_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    """Split a ``---``-delimited frontmatter block into fields and the body.

    The format is the plain ``key: value`` subset the skill convention needs. A
    document without a frontmatter block, without its closing fence, with a
    non-mapping line or a duplicate key is rejected as ``None``.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    fields: dict[str, str] = {}
    index = 1
    while index < len(lines) and lines[index].strip() != "---":
        line = lines[index]
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            return None
        normalized_key = key.strip()
        if normalized_key in fields:
            return None
        fields[normalized_key] = _frontmatter_value(value)
        index += 1
    if index >= len(lines):
        return None
    body = "".join(lines[index + 1 :])
    return fields, body


def discover_skills(
    boundary: WorkspaceBoundary,
) -> tuple[tuple[SkillInfo, ...], tuple[SkillDiagnostic, ...]]:
    """Scan ``.agents/skills/*/SKILL.md`` one level deep and validate each frontmatter.

    Invalid candidates are skipped with a typed diagnostic instead of failing the
    run; subdirectories are never scanned recursively.
    """
    skills: list[SkillInfo] = []
    diagnostics: list[SkillDiagnostic] = []
    root = boundary.root / SKILLS_DIRECTORY
    try:
        entries = sorted(root.iterdir(), key=lambda path: path.name)
    except (NotADirectoryError, OSError):
        return (), ()
    for entry in entries:
        relative = f"{SKILLS_DIRECTORY}/{entry.name}"
        if not entry.is_dir():
            continue
        parsed = _read_skill_markdown(boundary, relative)
        if isinstance(parsed, SkillDiagnostic):
            diagnostics.append(parsed)
            continue
        fields, _body, file_count = parsed
        failure = _validate_frontmatter(fields, entry.name)
        if failure is not None:
            diagnostics.append(SkillDiagnostic(relative, *failure))
            continue
        skills.append(
            SkillInfo(
                name=fields["name"],
                description=fields["description"],
                directory=relative,
                file_count=file_count,
            )
        )
    return tuple(skills), tuple(diagnostics)


def scan_workspace(boundary: WorkspaceBoundary) -> WorkspaceScan:
    """Freeze the instructions and skill index this run will use."""
    loaded = _load_workspace_instructions(boundary)
    instructions = loaded[1] if loaded is not None else None
    instructions_path = loaded[0] if loaded is not None else None
    skills, diagnostics = discover_skills(boundary)
    return WorkspaceScan(
        instructions=instructions,
        skills=skills,
        diagnostics=diagnostics,
        instructions_path=instructions_path,
    )


def render_workspace_sections(scan: WorkspaceScan) -> str:
    """Render the prompt sections for the scan; empty when the workspace has neither."""
    sections: list[str] = []
    if scan.instructions:
        sections.append(f"{INSTRUCTIONS_HEADER}\n\n{scan.instructions}")
    if scan.skills:
        lines = [SKILLS_HEADER, ""]
        lines.extend(f"- {skill.name}: {skill.description}" for skill in scan.skills)
        lines.extend(("", SKILL_LOAD_HINT))
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _directory_lists_name(root: Path, filename: str) -> bool:
    """Require the directory itself to list the exact name, case-sensitively."""
    try:
        return filename in {entry.name for entry in root.iterdir()}
    except OSError:
        return False


def _frontmatter_value(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _read_skill_markdown(
    boundary: WorkspaceBoundary, relative: str
) -> tuple[dict[str, str], str, int] | SkillDiagnostic:
    """Read one candidate's SKILL.md strictly through the workspace path boundary.

    Routing the read through ``boundary.resolve`` matters because discovery
    otherwise works on directory-listing paths: a skill directory that is a
    symlink pointing outside the workspace would sail past a plain path join.
    Resolution rejects such escapes with a typed diagnostic, and a missing
    SKILL.md surfaces as ``SKILL_MD_MISSING`` rather than a generic failure.
    """
    try:
        target = boundary.resolve(f"{relative}/{SKILL_MD_FILENAME}")
    except WorkspacePathError as error:
        if error.code == "PATH_NOT_FOUND":
            return SkillDiagnostic(relative, "SKILL_MD_MISSING", "skill directory has no SKILL.md")
        return SkillDiagnostic(relative, error.code, error.message)
    try:
        raw = target.read_bytes()
        file_count = sum(1 for item in target.parent.iterdir() if item.is_file())
    except OSError:
        return SkillDiagnostic(relative, "SKILL_MD_UNREADABLE", "unable to read SKILL.md")
    if b"\0" in raw:
        return SkillDiagnostic(relative, "SKILL_MD_UNREADABLE", "SKILL.md is not UTF-8 text")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return SkillDiagnostic(relative, "SKILL_MD_UNREADABLE", "SKILL.md is not UTF-8 text")
    parsed = parse_frontmatter(text)
    if parsed is None:
        return SkillDiagnostic(relative, "MISSING_FRONTMATTER", "SKILL.md has no valid frontmatter")
    fields, body = parsed
    return fields, body, file_count


def _validate_frontmatter(fields: Mapping[str, str], directory_name: str) -> tuple[str, str] | None:
    name = fields.get("name")
    if name is None:
        return ("MISSING_NAME", "frontmatter requires a name field")
    if not SKILL_NAME_PATTERN.match(name):
        return (
            "INVALID_NAME",
            "name must be lowercase alphanumeric segments separated by single hyphens",
        )
    if name != directory_name:
        return ("NAME_DIRECTORY_MISMATCH", f"name must equal the directory name {directory_name!r}")
    description = fields.get("description", _MISSING)
    if description is _MISSING:
        return ("MISSING_DESCRIPTION", "frontmatter requires a description field")
    if not description or len(description) > SKILL_DESCRIPTION_MAX_CHARS:
        return (
            "INVALID_DESCRIPTION",
            f"description must be 1-{SKILL_DESCRIPTION_MAX_CHARS} characters",
        )
    return None


__all__ = [
    "AGENTS_MD_FILENAMES",
    "AGENTS_MD_MAX_BYTES",
    "INSTRUCTIONS_HEADER",
    "SKILLS_DIRECTORY",
    "SKILLS_HEADER",
    "SKILL_DESCRIPTION_MAX_CHARS",
    "SKILL_DIAGNOSTIC_EVENT_TYPE",
    "SKILL_MD_FILENAME",
    "SkillDiagnostic",
    "SkillInfo",
    "WorkspaceScan",
    "discover_skills",
    "load_workspace_instructions",
    "parse_frontmatter",
    "render_workspace_sections",
    "scan_workspace",
]
