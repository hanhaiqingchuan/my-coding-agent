from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.tools.paths import WorkspaceBoundary
from coding_agent.workspace_context import (
    AGENTS_MD_MAX_BYTES,
    INSTRUCTIONS_HEADER,
    SKILLS_HEADER,
    SkillDiagnostic,
    SkillInfo,
    WorkspaceScan,
    discover_skills,
    load_workspace_instructions,
    parse_frontmatter,
    render_workspace_sections,
    scan_workspace,
)


def _boundary(tmp_path: Path) -> WorkspaceBoundary:
    return WorkspaceBoundary(tmp_path)


def _write_skill(
    workspace: Path,
    directory: str,
    frontmatter: str,
    body: str = "Run the described workflow.\n",
    companions: tuple[str, ...] = (),
) -> None:
    skill_dir = workspace / ".agents" / "skills" / directory
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    for companion in companions:
        (skill_dir / companion).write_text(f"companion {companion}\n", encoding="utf-8")


# --- AGENTS.md loading ------------------------------------------------------------------------


def test_agents_md_loads_through_the_workspace_boundary(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("所有回复以'收到'开头\n", encoding="utf-8")

    content = load_workspace_instructions(_boundary(tmp_path))

    assert content == "所有回复以'收到'开头\n"


def test_lowercase_agents_md_is_also_accepted(tmp_path: Path) -> None:
    (tmp_path / "agents.md").write_text("lowercase instructions", encoding="utf-8")

    assert load_workspace_instructions(_boundary(tmp_path)) == "lowercase instructions"


def test_uppercase_agents_md_wins_when_both_exist(tmp_path: Path) -> None:
    probe = tmp_path / "CASE-PROBE.md"
    probe.write_text("", encoding="utf-8")
    if (tmp_path / "case-probe.md").exists():
        pytest.skip("case-insensitive filesystem: both names cannot coexist")
    (tmp_path / "AGENTS.md").write_text("canonical", encoding="utf-8")
    (tmp_path / "agents.md").write_text("lowercase", encoding="utf-8")

    assert load_workspace_instructions(_boundary(tmp_path)) == "canonical"


@pytest.mark.parametrize("filename", ["Agents.md", "AGENTS.MD", "agents.MD"])
def test_other_agent_md_casings_are_not_loaded(tmp_path: Path, filename: str) -> None:
    (tmp_path / filename).write_text("mismatched case", encoding="utf-8")

    assert load_workspace_instructions(_boundary(tmp_path)) is None


def test_missing_agents_md_returns_none_without_error(tmp_path: Path) -> None:
    assert load_workspace_instructions(_boundary(tmp_path)) is None


def test_blank_agents_md_is_treated_as_absent(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("   \n\n", encoding="utf-8")

    assert load_workspace_instructions(_boundary(tmp_path)) is None


def test_oversize_agents_md_is_truncated_with_a_marker(tmp_path: Path) -> None:
    original = "内" * (AGENTS_MD_MAX_BYTES // 3 + 5_000)
    (tmp_path / "AGENTS.md").write_text(original, encoding="utf-8")

    content = load_workspace_instructions(_boundary(tmp_path))

    assert content is not None
    assert content.startswith("内" * 100)
    assert "truncated" in content
    assert len(content.encode("utf-8")) <= AGENTS_MD_MAX_BYTES + 200


def test_agents_md_pointing_outside_the_workspace_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-agents.md"
    outside.write_text("secret outside instructions", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to(outside)

    assert load_workspace_instructions(_boundary(tmp_path)) is None


def test_directory_named_agents_md_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").mkdir()

    assert load_workspace_instructions(_boundary(tmp_path)) is None


# --- frontmatter parsing ----------------------------------------------------------------------


def test_parse_frontmatter_extracts_fields_and_body() -> None:
    parsed = parse_frontmatter(
        "---\nname: git-helper\ndescription: Guide Git work.\n---\n\nBody.\n"
    )

    assert parsed is not None
    frontmatter, body = parsed
    assert frontmatter == {"name": "git-helper", "description": "Guide Git work."}
    assert body == "\nBody.\n"


@pytest.mark.parametrize(
    "text",
    [
        "no frontmatter at all\n",
        "---\nname: git-helper\n",
        "---\nnot a mapping line\n---\nbody\n",
        "name: git-helper\n---\nbody\n",
        "---\n: novalue\n---\nbody\n",
    ],
)
def test_parse_frontmatter_rejects_malformed_documents(text: str) -> None:
    assert parse_frontmatter(text) is None


def test_parse_frontmatter_ignores_extra_keys_but_rejects_duplicates() -> None:
    parsed = parse_frontmatter("---\nname: a\ndescription: d\nextra: value\n---\nbody\n")
    assert parsed is not None
    assert parsed[0] == {"name": "a", "description": "d", "extra": "value"}

    assert parse_frontmatter("---\nname: a\nname: b\ndescription: d\n---\nbody\n") is None


# --- skill discovery --------------------------------------------------------------------------


def test_discovery_collects_valid_skills_with_file_counts(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "git-helper",
        "name: git-helper\ndescription: Guide routine Git operations.",
        companions=("refs.md", "run.sh"),
    )
    _write_skill(tmp_path, "code-review", "name: code-review\ndescription: Review a diff.")

    skills, diagnostics = discover_skills(_boundary(tmp_path))

    assert diagnostics == ()
    assert skills == (
        SkillInfo(
            name="code-review",
            description="Review a diff.",
            directory=".agents/skills/code-review",
            file_count=1,
        ),
        SkillInfo(
            name="git-helper",
            description="Guide routine Git operations.",
            directory=".agents/skills/git-helper",
            file_count=3,
        ),
    )


def test_discovery_without_a_skills_directory_returns_nothing(tmp_path: Path) -> None:
    skills, diagnostics = discover_skills(_boundary(tmp_path))

    assert skills == ()
    assert diagnostics == ()


@pytest.mark.parametrize(
    ("frontmatter", "code"),
    [
        ("description: Guide Git work.", "MISSING_NAME"),
        ("name: Git_Helper\ndescription: d", "INVALID_NAME"),
        ("name: git helper\ndescription: d", "INVALID_NAME"),
        ("name: -git-helper\ndescription: d", "INVALID_NAME"),
        ("name: other-name\ndescription: d", "NAME_DIRECTORY_MISMATCH"),
        ("name: git-helper", "MISSING_DESCRIPTION"),
        ("name: git-helper\ndescription: ''", "INVALID_DESCRIPTION"),
        ("name: git-helper\ndescription:    ", "INVALID_DESCRIPTION"),
        ("name: git-helper\ndescription: " + "x" * 1_025, "INVALID_DESCRIPTION"),
    ],
)
def test_invalid_skills_are_skipped_with_a_typed_diagnostic(
    tmp_path: Path, frontmatter: str, code: str
) -> None:
    _write_skill(tmp_path, "git-helper", frontmatter)

    skills, diagnostics = discover_skills(_boundary(tmp_path))

    assert skills == ()
    assert len(diagnostics) == 1
    assert diagnostics[0].directory == ".agents/skills/git-helper"
    assert diagnostics[0].code == code
    assert diagnostics[0].message


def test_malformed_or_missing_skill_md_publishes_a_diagnostic(tmp_path: Path) -> None:
    _write_skill(tmp_path, "broken", "name: broken")
    (tmp_path / ".agents" / "skills" / "empty-dir").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "no-frontmatter").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "no-frontmatter" / "SKILL.md").write_text(
        "just a body\n", encoding="utf-8"
    )

    skills, diagnostics = discover_skills(_boundary(tmp_path))

    assert skills == ()
    codes = {diagnostic.directory: diagnostic.code for diagnostic in diagnostics}
    assert codes[".agents/skills/broken"] == "MISSING_DESCRIPTION"
    assert codes[".agents/skills/empty-dir"] == "SKILL_MD_MISSING"
    assert codes[".agents/skills/no-frontmatter"] == "MISSING_FRONTMATTER"


def test_discovery_does_not_scan_skill_subdirectories_recursively(tmp_path: Path) -> None:
    _write_skill(tmp_path, "outer", "name: outer\ndescription: outer skill")
    nested = tmp_path / ".agents" / "skills" / "outer" / "nested-skill"
    nested.mkdir()
    (nested / "SKILL.md").write_text(
        "---\nname: nested-skill\ndescription: must not be found\n---\nbody\n", encoding="utf-8"
    )

    skills, diagnostics = discover_skills(_boundary(tmp_path))

    assert [skill.name for skill in skills] == ["outer"]
    assert diagnostics == ()
    assert skills[0].file_count == 1  # only SKILL.md; the nested directory is not a file


def test_discovery_ignores_plain_files_in_the_skills_directory(tmp_path: Path) -> None:
    skills_root = tmp_path / ".agents" / "skills"
    skills_root.mkdir(parents=True)
    (skills_root / "notes.txt").write_text("stray file\n", encoding="utf-8")

    skills, diagnostics = discover_skills(_boundary(tmp_path))

    assert skills == ()
    assert diagnostics == ()


def test_discovery_rejects_a_skill_directory_symlinked_outside_the_workspace(
    tmp_path: Path,
) -> None:
    """Directory-listing paths bypass nothing: the SKILL.md read goes through the boundary."""
    outside = tmp_path.parent / "outside-skill-directory"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        "---\nname: evil\ndescription: escape the workspace boundary.\n---\nsmuggled\n",
        encoding="utf-8",
    )
    skills_root = tmp_path / ".agents" / "skills"
    skills_root.mkdir(parents=True)
    (skills_root / "evil").symlink_to(outside)

    skills, diagnostics = discover_skills(_boundary(tmp_path))

    assert skills == ()
    assert len(diagnostics) == 1
    assert diagnostics[0].directory == ".agents/skills/evil"
    assert diagnostics[0].code == "PATH_OUTSIDE_WORKSPACE"


# --- scan + system prompt rendering ------------------------------------------------------------


def test_scan_workspace_combines_instructions_and_skills(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("回答保持简洁。", encoding="utf-8")
    _write_skill(tmp_path, "git-helper", "name: git-helper\ndescription: Guide Git work.")

    scan = scan_workspace(_boundary(tmp_path))

    assert scan == WorkspaceScan(
        instructions="回答保持简洁。",
        skills=(
            SkillInfo(
                name="git-helper",
                description="Guide Git work.",
                directory=".agents/skills/git-helper",
                file_count=1,
            ),
        ),
        diagnostics=(),
    )


def test_render_sections_carries_both_headers_and_the_load_hint(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("回答保持简洁。", encoding="utf-8")
    _write_skill(tmp_path, "git-helper", "name: git-helper\ndescription: Guide Git work.")

    rendered = render_workspace_sections(scan_workspace(_boundary(tmp_path)))

    assert INSTRUCTIONS_HEADER in rendered
    assert "回答保持简洁。" in rendered
    assert SKILLS_HEADER in rendered
    assert "- git-helper: Guide Git work." in rendered
    assert "skill" in rendered
    assert "read" in rendered


def test_render_sections_is_empty_without_instructions_or_skills(tmp_path: Path) -> None:
    assert render_workspace_sections(scan_workspace(_boundary(tmp_path))) == ""


def test_render_sections_lists_skills_without_instructions(tmp_path: Path) -> None:
    _write_skill(tmp_path, "git-helper", "name: git-helper\ndescription: Guide Git work.")

    rendered = render_workspace_sections(scan_workspace(_boundary(tmp_path)))

    assert INSTRUCTIONS_HEADER not in rendered
    assert "- git-helper: Guide Git work." in rendered


def test_diagnostic_payload_is_json_ready(tmp_path: Path) -> None:
    diagnostic = SkillDiagnostic(
        ".agents/skills/x", "MISSING_DESCRIPTION", "description is required"
    )

    assert diagnostic.payload() == {
        "skill": ".agents/skills/x",
        "code": "MISSING_DESCRIPTION",
        "message": "description is required",
    }
