"""Shared builders for offline evaluation fixtures.

Every fixture here stays deterministic and offline: baselines are three-file Python
trees, and oracles are stdlib-only scripts executed with the running interpreter.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from coding_agent.evaluation.manifest import content_hash, tree_files, tree_hash

TARGET_ORACLE = '''"""Fail unless the candidate workspace exposes VALUE == 2."""

import sys
from pathlib import Path

workspace = Path(sys.argv[1])
text = (workspace / "src" / "mod.py").read_text(encoding="utf-8")
sys.exit(0 if "VALUE = 2" in text else 1)
'''

REGRESSION_ORACLE = '''"""Fail unless the pre-existing helper survived the change."""

import sys
from pathlib import Path

workspace = Path(sys.argv[1])
text = (workspace / "src" / "helper.py").read_text(encoding="utf-8")
sys.exit(0 if "def helper()" in text else 1)
'''

CRASHING_ORACLE = '''"""Exit with a reserved code so the harness reports an oracle error."""

import sys

sys.exit(7)
'''


def write_task_tree(root: Path, task_id: str, *, with_error_overlay: bool = True) -> Path:
    """Create one baseline/gold/error/oracle task directory under ``root``."""
    base = root / task_id
    baseline = base / "baseline"
    (baseline / "src").mkdir(parents=True)
    (baseline / "tests").mkdir(parents=True)
    (baseline / "src" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (baseline / "src" / "helper.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (baseline / "tests" / "keep.txt").write_text("keep\n", encoding="utf-8")
    (baseline / "README.md").write_text("baseline\n", encoding="utf-8")

    gold = base / "gold" / "src"
    gold.mkdir(parents=True)
    (gold / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")

    if with_error_overlay:
        error = base / "error" / "src"
        error.mkdir(parents=True)
        (error / "mod.py").write_text("VALUE = 3\n", encoding="utf-8")

    oracle = base / "oracle"
    oracle.mkdir(parents=True)
    (oracle / "target.py").write_text(TARGET_ORACLE, encoding="utf-8")
    (oracle / "regression.py").write_text(REGRESSION_ORACLE, encoding="utf-8")

    (base / "prompt.md").write_text("Change VALUE to 2 in src/mod.py.\n", encoding="utf-8")
    return base


def task_table(
    task_id: str,
    root: Path,
    *,
    category: str = "local_edit",
    overrides: dict[str, object] | None = None,
    drop: tuple[str, ...] = (),
) -> str:
    """Render one ``[[tasks]]`` table so tests can mutate a single field."""
    base = root / task_id
    values: dict[str, object] = {
        "task_id": task_id,
        "category": category,
        "prompt": f"{task_id}/prompt.md",
        "baseline": f"{task_id}/baseline",
        "baseline_tree_hash": tree_hash(tree_files(base / "baseline")),
        "gold_overlay": f"{task_id}/gold",
        "error_overlay": f"{task_id}/error",
        "target_oracle": f"{task_id}/oracle/target.py",
        "target_oracle_hash": content_hash((base / "oracle" / "target.py").read_bytes()),
        "regression_oracle": f"{task_id}/oracle/regression.py",
        "regression_oracle_hash": content_hash((base / "oracle" / "regression.py").read_bytes()),
        "allowed_paths": ["src"],
        "forbidden_paths": ["README.md"],
        "timeout_seconds": 60,
        "commands": [{"command": "true", "cwd": "."}],
    }
    values.update(overrides or {})
    for name in drop:
        values.pop(name, None)

    lines = ["[[tasks]]"]
    for key, value in values.items():
        lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        body = ", ".join(f"{key} = {_toml_value(item)}" for key, item in value.items())
        return "{" + body + "}"
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")


def write_manifest(
    root: Path,
    *,
    schema_version: str = "evaluation-manifest-v1",
    manifest_id: str = "offline-fixture",
    tasks: str | None = None,
    extra: str = "",
) -> Path:
    """Write a manifest file next to the task directories it references."""
    body = [f'schema_version = "{schema_version}"', f'manifest_id = "{manifest_id}"']
    if extra:
        body.append(extra)
    body.append("")
    body.append(tasks if tasks is not None else task_table("demo-task", root))
    path = root / "manifest.toml"
    path.write_text("\n".join(body), encoding="utf-8")
    return path


@pytest.fixture
def manifest_root(tmp_path: Path) -> Path:
    """A directory containing exactly one valid task tree named ``demo-task``."""
    root = tmp_path / "manifest-root"
    root.mkdir()
    write_task_tree(root, "demo-task")
    return root


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text("[agent]\nmax_rounds = 4\n", encoding="utf-8")
    return path


def apply_overlay(overlay: Path, destination: Path) -> None:
    shutil.copytree(overlay, destination, dirs_exist_ok=True)


__all__ = [
    "CRASHING_ORACLE",
    "REGRESSION_ORACLE",
    "TARGET_ORACLE",
    "apply_overlay",
    "task_table",
    "write_manifest",
    "write_task_tree",
]
