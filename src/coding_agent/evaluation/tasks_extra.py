"""Composition checks for the expanded public task set.

The manifest validator proves that every task is complete and in bounds; this module
proves that the delivered public set is the full matrix of design section 18.4 —
four categories with three tasks each — so a campaign report can honestly claim to
cover the whole category space.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from coding_agent.evaluation.manifest import (
    CATEGORIES,
    EvaluationManifest,
    ManifestError,
    validate_manifest,
)

MANIFEST_FILENAME = "manifest.toml"
TASKS_PER_CATEGORY = 3
TOTAL_TASKS = len(CATEGORIES) * TASKS_PER_CATEGORY


def build_task_set(public_dir: Path) -> list[EvaluationManifest]:
    """Validate every manifest under ``public_dir`` and prove the delivered composition.

    Returns the validated manifests (currently the single ``public-p0`` manifest), so
    callers iterate every delivered task through one stable entry point. The combined
    task set must be exactly the section 18.4 matrix: every category present, three
    tasks each, no duplicates, and every task carrying the error variant the
    three-state validation needs.
    """
    manifest_path = public_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ManifestError(f"manifest: file not found: {manifest_path}")
    manifests = [validate_manifest(manifest_path)]
    _require_full_matrix(manifests)
    return manifests


def _require_full_matrix(manifests: list[EvaluationManifest]) -> None:
    tasks = [task for manifest in manifests for task in manifest.tasks]
    counts = Counter(task.category for task in tasks)
    expected = {category: TASKS_PER_CATEGORY for category in CATEGORIES}
    if counts != expected:
        raise ManifestError(
            "tasks: the public set must hold exactly "
            f"{TASKS_PER_CATEGORY} tasks in each of {len(CATEGORIES)} categories "
            f"({TOTAL_TASKS} total), found {dict(sorted(counts.items()))}"
        )
    seen: set[str] = set()
    for task in tasks:
        if task.task_id in seen:
            raise ManifestError(f"tasks: duplicate task id across manifests: {task.task_id}")
        seen.add(task.task_id)
        if task.error_overlay is None:
            raise ManifestError(
                f"tasks: {task.task_id}: the public set requires an error overlay "
                "for three-state validation"
            )


__all__ = [
    "MANIFEST_FILENAME",
    "TASKS_PER_CATEGORY",
    "TOTAL_TASKS",
    "build_task_set",
]
