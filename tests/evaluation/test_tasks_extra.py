"""Offline tests for the expanded public task set and its composition check."""

from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.evaluation.manifest import ManifestError, validate_manifest
from coding_agent.evaluation.runner import verify_task_setup
from coding_agent.evaluation.tasks_extra import TASKS_PER_CATEGORY, build_task_set
from tests.evaluation.conftest import task_table, write_manifest, write_task_tree

PUBLIC_TASKS = Path(__file__).resolve().parents[2] / "evaluation" / "tasks" / "public"


def test_build_task_set_returns_the_twelve_delivered_tasks() -> None:
    """The public set is the full section 18.4 matrix: four categories, three each."""
    manifests = build_task_set(PUBLIC_TASKS)

    tasks = [task for manifest in manifests for task in manifest.tasks]
    assert len(tasks) == 12
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.category] = counts.get(task.category, 0) + 1
    assert counts == {
        "new_file": TASKS_PER_CATEGORY,
        "local_edit": TASKS_PER_CATEGORY,
        "locate_and_modify": TASKS_PER_CATEGORY,
        "large_file_edit": TASKS_PER_CATEGORY,
    }
    assert all(task.error_overlay is not None for task in tasks)
    assert all(task.commands for task in tasks)
    assert len({task.task_id for task in tasks}) == 12


def test_build_task_set_keeps_the_four_pinned_p0_tasks_unchanged() -> None:
    """The expansion adds tasks; it must not disturb the four original ones."""
    manifest = validate_manifest(PUBLIC_TASKS / "manifest.toml")
    original = {
        "new-file-slugify": "7b43c81858e4e878f765d843fdff5d24d3770161422d02efe67c92d4475f5f89",
        "local-edit-clamp": "08608de3259c427b37a71c28f675fad3b912da1c8ebd72e443d184d2341575c8",
        "locate-two-files": "d5d4cdcde3b36f28eeb5227b96c33f3a6a0bc909ec829e865a0d1da49cc8662c",
        "large-file-handlers": "9176b6c39c13ad6d6c20cb3bec256a3dcbfd6286ba382e631a1035a6055ce7a1",
    }

    by_id = {task.task_id: task.baseline_tree_hash for task in manifest.tasks}

    assert {name: by_id[name] for name in original} == original


@pytest.mark.parametrize(
    "task_id",
    [
        "new-file-slugify",
        "new-file-wordcount",
        "new-file-roman",
        "local-edit-clamp",
        "local-edit-mean",
        "local-edit-suffix",
        "locate-two-files",
        "locate-step-normalize",
        "locate-rename-timeout",
        "large-file-handlers",
        "large-file-routes",
        "large-file-validators",
    ],
)
def test_every_public_task_passes_three_state_validation(tmp_path: Path, task_id: str) -> None:
    """Baseline fails, gold passes, error variant fails — for all twelve tasks."""
    manifest = validate_manifest(PUBLIC_TASKS / "manifest.toml")
    task = next(item for item in manifest.tasks if item.task_id == task_id)

    verification = verify_task_setup(task, scratch=tmp_path / "scratch" / task_id)

    assert verification.ok is True, verification.detail
    assert verification.baseline_failed is True
    assert verification.baseline_regression_passed is True
    assert verification.gold_passed is True
    assert verification.gold_regression_passed is True
    assert verification.error_variant_failed is True


def test_build_task_set_rejects_an_incomplete_task_set(tmp_path: Path) -> None:
    """A public directory whose manifest is not the full matrix must not pass silently."""
    root = tmp_path / "public"
    root.mkdir()
    write_task_tree(root, "demo-absent")
    write_manifest(root, tasks=task_table("demo-absent", root))

    with pytest.raises(ManifestError, match="task"):
        build_task_set(root)
