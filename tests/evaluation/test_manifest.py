from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.evaluation.manifest import ManifestError, validate_manifest
from tests.evaluation.conftest import task_table, write_manifest, write_task_tree

PUBLIC_MANIFEST = Path(__file__).resolve().parents[2] / "evaluation" / "tasks" / "public"


def test_validate_manifest_resolves_task_paths_inside_the_manifest_directory(
    manifest_root: Path,
) -> None:
    """Unresolved manifest paths would let a campaign read files it never declared."""
    path = write_manifest(manifest_root)

    manifest = validate_manifest(path)

    assert manifest.schema_version == "evaluation-manifest-v1"
    assert manifest.manifest_id == "offline-fixture"
    assert [task.task_id for task in manifest.tasks] == ["demo-task"]
    task = manifest.tasks[0]
    assert task.prompt == manifest_root / "demo-task" / "prompt.md"
    assert task.baseline == manifest_root / "demo-task" / "baseline"
    assert task.target_oracle == manifest_root / "demo-task" / "oracle" / "target.py"
    assert task.commands == ({"command": "true", "cwd": "."},)
    assert task.category == "local_edit"


def test_unknown_schema_version_is_rejected(manifest_root: Path) -> None:
    """Accepting an unknown version would silently reinterpret task semantics."""
    path = write_manifest(manifest_root, schema_version="evaluation-manifest-v2")

    with pytest.raises(ManifestError, match="schema_version"):
        validate_manifest(path)


@pytest.mark.parametrize(
    "field",
    ["prompt", "baseline", "gold_overlay", "target_oracle", "regression_oracle", "error_overlay"],
)
@pytest.mark.parametrize("escape", ["../outside", "/etc/passwd", "demo-task/../../outside"])
def test_path_escapes_are_rejected(manifest_root: Path, field: str, escape: str) -> None:
    """A manifest path outside its own directory could exfiltrate unrelated files."""
    path = write_manifest(
        manifest_root,
        tasks=task_table("demo-task", manifest_root, overrides={field: escape}),
    )

    with pytest.raises(ManifestError, match=field):
        validate_manifest(path)


@pytest.mark.parametrize(
    "field",
    ["prompt", "baseline", "gold_overlay", "target_oracle", "regression_oracle"],
)
def test_missing_baseline_or_oracle_is_rejected(manifest_root: Path, field: str) -> None:
    """A campaign that starts without its baseline or oracle cannot be scored."""
    path = write_manifest(
        manifest_root,
        tasks=task_table("demo-task", manifest_root, overrides={field: "demo-task/absent"}),
    )

    with pytest.raises(ManifestError, match=field):
        validate_manifest(path)


@pytest.mark.parametrize(
    "field",
    [
        "prompt",
        "baseline",
        "gold_overlay",
        "target_oracle",
        "regression_oracle",
        "allowed_paths",
        "baseline_tree_hash",
        "target_oracle_hash",
        "regression_oracle_hash",
    ],
)
def test_required_task_fields_must_be_present(manifest_root: Path, field: str) -> None:
    """Defaulting a missing field would hide an incomplete task definition."""
    path = write_manifest(
        manifest_root,
        tasks=task_table("demo-task", manifest_root, drop=(field,)),
    )

    with pytest.raises(ManifestError, match=field):
        validate_manifest(path)


def test_empty_command_allowlist_is_rejected(manifest_root: Path) -> None:
    """An empty allowlist with --yes would leave the effect gate undefined."""
    path = write_manifest(
        manifest_root,
        tasks=task_table("demo-task", manifest_root, overrides={"commands": []}),
    )

    with pytest.raises(ManifestError, match="commands"):
        validate_manifest(path)


@pytest.mark.parametrize(
    "entry",
    [
        {"command": "true", "cwd": ".."},
        {"command": "true", "cwd": "/tmp"},
        {"command": "true", "cwd": "absent"},
        {"command": "   ", "cwd": "."},
        {"command_prefix": "tru", "cwd": "."},
    ],
)
def test_out_of_bounds_command_allowlist_entries_are_rejected(
    manifest_root: Path,
    entry: dict[str, str],
) -> None:
    """Prefix or escaping allowlist entries would authorize unlisted effects."""
    path = write_manifest(
        manifest_root,
        tasks=task_table("demo-task", manifest_root, overrides={"commands": [entry]}),
    )

    with pytest.raises(ManifestError, match="commands"):
        validate_manifest(path)


def test_duplicate_task_ids_are_rejected(manifest_root: Path) -> None:
    """Duplicate ids would collide on the immutable per-run output directory."""
    write_task_tree(manifest_root, "other-task")
    path = write_manifest(
        manifest_root,
        tasks=task_table("demo-task", manifest_root)
        + "\n"
        + task_table("demo-task", manifest_root),
    )

    with pytest.raises(ManifestError, match="task_id"):
        validate_manifest(path)


def test_unknown_category_is_rejected(manifest_root: Path) -> None:
    """An unknown category would break per-category reporting in the summary."""
    path = write_manifest(
        manifest_root,
        tasks=task_table("demo-task", manifest_root, overrides={"category": "refactor"}),
    )

    with pytest.raises(ManifestError, match="category"):
        validate_manifest(path)


def test_unknown_task_field_is_rejected(manifest_root: Path) -> None:
    """Silently ignoring an unknown field would hide a misspelled constraint."""
    path = write_manifest(
        manifest_root,
        tasks=task_table("demo-task", manifest_root, overrides={"protected_paths": ["src"]}),
    )

    with pytest.raises(ManifestError, match="protected_paths"):
        validate_manifest(path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"allowed_paths": []},
        {"allowed_paths": ["../src"]},
        {"forbidden_paths": ["src"]},
        {"timeout_seconds": 0},
        {"timeout_seconds": 100_000},
    ],
)
def test_out_of_bounds_scope_and_timeout_are_rejected(
    manifest_root: Path,
    overrides: dict[str, object],
) -> None:
    """Unbounded scope or timeout would make a run neither comparable nor safe."""
    path = write_manifest(
        manifest_root,
        tasks=task_table("demo-task", manifest_root, overrides=overrides),
    )

    with pytest.raises(ManifestError):
        validate_manifest(path)


def test_oracle_and_gold_must_live_outside_the_read_only_baseline(manifest_root: Path) -> None:
    """An oracle inside the baseline would be copied into the agent's own workspace."""
    inside = manifest_root / "demo-task" / "baseline" / "oracle"
    inside.mkdir()
    (inside / "target.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    path = write_manifest(
        manifest_root,
        tasks=task_table(
            "demo-task",
            manifest_root,
            overrides={"target_oracle": "demo-task/baseline/oracle/target.py"},
        ),
    )

    with pytest.raises(ManifestError, match="baseline"):
        validate_manifest(path)


def test_unknown_top_level_field_is_rejected(manifest_root: Path) -> None:
    """A stray top-level key usually means an operator edited the wrong schema."""
    path = write_manifest(manifest_root, extra='results_dir = "/tmp/results"')

    with pytest.raises(ManifestError, match="results_dir"):
        validate_manifest(path)


def test_manifest_without_tasks_is_rejected(manifest_root: Path) -> None:
    """An empty campaign would report a vacuous completion rate."""
    path = write_manifest(manifest_root, tasks="")

    with pytest.raises(ManifestError, match="tasks"):
        validate_manifest(path)


@pytest.mark.parametrize("edit", ["modify", "add"])
def test_edited_baseline_no_longer_matches_its_recorded_tree_hash(
    manifest_root: Path,
    edit: str,
) -> None:
    """An unpinned baseline could drift between campaigns and silently change the task."""
    path = write_manifest(manifest_root)
    baseline = manifest_root / "demo-task" / "baseline"
    if edit == "modify":
        (baseline / "src" / "mod.py").write_text("VALUE = 7\n", encoding="utf-8")
    else:
        (baseline / "src" / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")

    with pytest.raises(ManifestError, match="baseline_tree_hash"):
        validate_manifest(path)


@pytest.mark.parametrize(
    "field, entry",
    [
        ("target_oracle_hash", "target.py"),
        ("regression_oracle_hash", "regression.py"),
    ],
)
def test_edited_oracle_no_longer_matches_its_recorded_hash(
    manifest_root: Path,
    field: str,
    entry: str,
) -> None:
    """An unpinned oracle could be relaxed after publication without leaving a trace."""
    path = write_manifest(manifest_root)
    (manifest_root / "demo-task" / "oracle" / entry).write_text(
        "import sys\nsys.exit(0)\n", encoding="utf-8"
    )

    with pytest.raises(ManifestError, match=field):
        validate_manifest(path)


@pytest.mark.parametrize(
    "field",
    ["baseline_tree_hash", "target_oracle_hash", "regression_oracle_hash"],
)
def test_recorded_hashes_must_be_sha256_digests(manifest_root: Path, field: str) -> None:
    """A truncated or placeholder digest would make the pin unverifiable."""
    path = write_manifest(
        manifest_root,
        tasks=task_table("demo-task", manifest_root, overrides={field: "not-a-digest"}),
    )

    with pytest.raises(ManifestError, match=field):
        validate_manifest(path)


def test_public_manifest_declares_three_tasks_per_category() -> None:
    """The delivered task set must cover all four spec categories exactly three times."""
    manifest = validate_manifest(PUBLIC_MANIFEST / "manifest.toml")

    categories = sorted(task.category for task in manifest.tasks)
    assert categories == [
        "large_file_edit",
        "large_file_edit",
        "large_file_edit",
        "local_edit",
        "local_edit",
        "local_edit",
        "locate_and_modify",
        "locate_and_modify",
        "locate_and_modify",
        "new_file",
        "new_file",
        "new_file",
    ]
    assert all(task.error_overlay is not None for task in manifest.tasks)
    assert all(task.commands for task in manifest.tasks)
    pinned = [
        digest
        for task in manifest.tasks
        for digest in (
            task.baseline_tree_hash,
            task.target_oracle_hash,
            task.regression_oracle_hash,
        )
    ]
    assert len(pinned) == 36
    assert all(len(digest) == 64 for digest in pinned)
