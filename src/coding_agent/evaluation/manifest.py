"""Static validation of evaluation task manifests.

This module never executes a task. It only proves that a manifest describes a
complete, self-contained and in-bounds task set, so that every later stage can
assume its paths exist inside the manifest directory and its allowlist is exact.
It also owns the harness's single content-hashing implementation and uses it to
verify each task's pinned baseline tree and oracle hashes against the files on disk.
"""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from coding_agent.runtime.metrics import canonical_hash

SCHEMA_VERSION = "evaluation-manifest-v1"
CATEGORIES = ("new_file", "local_edit", "locate_and_modify", "large_file_edit")
MAX_TIMEOUT_SECONDS = 3_600
IGNORED_NAMES = frozenset({"__pycache__", ".git", ".DS_Store"})
_TOP_LEVEL_FIELDS = frozenset({"schema_version", "manifest_id", "tasks"})
_REQUIRED_TASK_FIELDS = (
    "task_id",
    "category",
    "prompt",
    "baseline",
    "baseline_tree_hash",
    "gold_overlay",
    "target_oracle",
    "target_oracle_hash",
    "regression_oracle",
    "regression_oracle_hash",
    "allowed_paths",
    "timeout_seconds",
    "commands",
)
_HASH_FIELDS = ("baseline_tree_hash", "target_oracle_hash", "regression_oracle_hash")
_HEX_DIGITS = frozenset("0123456789abcdef")
_OPTIONAL_TASK_FIELDS = ("error_overlay", "forbidden_paths")
_FILE_FIELDS = ("prompt", "target_oracle", "regression_oracle")
_DIRECTORY_FIELDS = ("baseline", "gold_overlay", "error_overlay")


class ManifestError(ValueError):
    """Raised when a manifest is missing, malformed, or out of bounds."""


def content_hash(data: bytes) -> str:
    """Hash one file's exact bytes; the harness has no second hashing implementation."""
    return hashlib.sha256(data).hexdigest()


def tree_files(root: Path) -> dict[str, str]:
    """Map every regular file below ``root`` to its content hash, ignoring build noise.

    Symlinks, ``__pycache__``, ``.git``, ``.DS_Store`` and byte-compiled files stay out, so
    a recorded tree hash survives anyone merely running the baseline's own test suite.
    """
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in IGNORED_NAMES for part in relative.parts) or path.suffix == ".pyc":
            continue
        files[relative.as_posix()] = content_hash(path.read_bytes())
    return files


def tree_hash(files: Mapping[str, str]) -> str:
    """Hash one tree's path-to-content-hash mapping over its canonical JSON encoding."""
    return canonical_hash(dict(sorted(files.items())))


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """One fully resolved evaluation task."""

    task_id: str
    category: str
    prompt: Path
    baseline: Path
    gold_overlay: Path
    target_oracle: Path
    regression_oracle: Path
    baseline_tree_hash: str
    target_oracle_hash: str
    regression_oracle_hash: str
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    timeout_seconds: int
    commands: tuple[Mapping[str, str], ...]
    error_overlay: Path | None = None


@dataclass(frozen=True, slots=True)
class EvaluationManifest:
    """A validated manifest plus the directory every task path resolves inside."""

    schema_version: str
    manifest_id: str
    root: Path
    path: Path
    tasks: tuple[TaskSpec, ...]


def validate_manifest(path: Path) -> EvaluationManifest:
    """Parse and fully validate one manifest without running any task."""
    root = path.resolve().parent
    raw = _load(path)
    unknown = sorted(set(raw) - _TOP_LEVEL_FIELDS)
    if unknown:
        raise ManifestError(f"manifest: unknown field: {unknown[0]}")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(f"manifest.schema_version: must be {SCHEMA_VERSION}")
    manifest_id = raw.get("manifest_id")
    if not isinstance(manifest_id, str) or not _is_slug(manifest_id):
        raise ManifestError("manifest.manifest_id: must be a lowercase slug")
    entries = raw.get("tasks")
    if not isinstance(entries, list) or not entries:
        raise ManifestError("manifest.tasks: must contain at least one task")

    tasks: list[TaskSpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        task = _task(entry, index, root)
        if task.task_id in seen:
            raise ManifestError(f"tasks[{index}].task_id: duplicate task id: {task.task_id}")
        seen.add(task.task_id)
        tasks.append(task)
    return EvaluationManifest(
        schema_version=SCHEMA_VERSION,
        manifest_id=manifest_id,
        root=root,
        path=path.resolve(),
        tasks=tuple(tasks),
    )


def _load(path: Path) -> Mapping[str, object]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as error:
        raise ManifestError(f"manifest: file not found: {path}") from error
    except OSError as error:
        raise ManifestError("manifest: unable to read the manifest file") from error
    except tomllib.TOMLDecodeError as error:
        raise ManifestError(f"manifest: invalid TOML: {error}") from error


def _task(entry: object, index: int, root: Path) -> TaskSpec:
    field = f"tasks[{index}]"
    if not isinstance(entry, dict):
        raise ManifestError(f"{field}: must be a TOML table")
    known = set(_REQUIRED_TASK_FIELDS) | set(_OPTIONAL_TASK_FIELDS)
    unknown = sorted(set(entry) - known)
    if unknown:
        raise ManifestError(f"{field}: unknown field: {unknown[0]}")
    missing = [name for name in _REQUIRED_TASK_FIELDS if name not in entry]
    if missing:
        raise ManifestError(f"{field}: missing required field: {missing[0]}")

    task_id = entry["task_id"]
    if not isinstance(task_id, str) or not _is_slug(task_id):
        raise ManifestError(f"{field}.task_id: must be a lowercase slug")
    category = entry["category"]
    if category not in CATEGORIES:
        raise ManifestError(f"{field}.category: must be one of {', '.join(CATEGORIES)}")

    resolved: dict[str, Path] = {}
    for name in _FILE_FIELDS:
        resolved[name] = _existing(entry[name], root, f"{field}.{name}", directory=False)
    for name in _DIRECTORY_FIELDS:
        value = entry.get(name)
        if value is None:
            continue
        resolved[name] = _existing(value, root, f"{field}.{name}", directory=True)

    baseline = resolved["baseline"]
    for name, path in resolved.items():
        if name != "baseline" and (path == baseline or baseline in path.parents):
            raise ManifestError(f"{field}.{name}: must stay outside the read-only baseline")
    allowed = _scope(entry["allowed_paths"], f"{field}.allowed_paths", required=True)
    forbidden = _scope(entry.get("forbidden_paths", []), f"{field}.forbidden_paths", required=False)
    overlap = sorted(set(allowed) & set(forbidden))
    if overlap:
        raise ManifestError(f"{field}.forbidden_paths: overlaps allowed_paths: {overlap[0]}")

    timeout = entry["timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise ManifestError(f"{field}.timeout_seconds: must be an integer")
    if not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise ManifestError(f"{field}.timeout_seconds: must be between 1 and {MAX_TIMEOUT_SECONDS}")

    recorded = {name: _digest(entry[name], f"{field}.{name}") for name in _HASH_FIELDS}
    _verify_pinned_inputs(
        field,
        recorded,
        baseline=baseline,
        target_oracle=resolved["target_oracle"],
        regression_oracle=resolved["regression_oracle"],
    )

    return TaskSpec(
        task_id=task_id,
        category=category,
        prompt=resolved["prompt"],
        baseline=baseline,
        gold_overlay=resolved["gold_overlay"],
        target_oracle=resolved["target_oracle"],
        regression_oracle=resolved["regression_oracle"],
        baseline_tree_hash=recorded["baseline_tree_hash"],
        target_oracle_hash=recorded["target_oracle_hash"],
        regression_oracle_hash=recorded["regression_oracle_hash"],
        allowed_paths=allowed,
        forbidden_paths=forbidden,
        timeout_seconds=timeout,
        commands=_commands(entry["commands"], f"{field}.commands", baseline),
        error_overlay=resolved.get("error_overlay"),
    )


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - _HEX_DIGITS:
        raise ManifestError(f"{field}: must be a 64-character lowercase sha256 hex digest")
    return value


def _verify_pinned_inputs(
    field: str,
    recorded: Mapping[str, str],
    *,
    baseline: Path,
    target_oracle: Path,
    regression_oracle: Path,
) -> None:
    """Prove the recorded hashes still describe the files this manifest points at.

    Without this check a baseline or an oracle could be edited between campaigns and every
    result would still claim to come from the pinned task.
    """
    on_disk = {
        "baseline_tree_hash": tree_hash(tree_files(baseline)),
        "target_oracle_hash": content_hash(target_oracle.read_bytes()),
        "regression_oracle_hash": content_hash(regression_oracle.read_bytes()),
    }
    for name, expected in recorded.items():
        if on_disk[name] != expected:
            raise ManifestError(
                f"{field}.{name}: recorded {expected} does not match the content on disk "
                f"({on_disk[name]})"
            )


def _existing(value: object, root: Path, field: str, *, directory: bool) -> Path:
    relative = _relative(value, field)
    resolved = (root / relative).resolve()
    if resolved != root and root not in resolved.parents:
        raise ManifestError(f"{field}: must stay inside the manifest directory")
    if directory and not resolved.is_dir():
        raise ManifestError(f"{field}: must name an existing directory")
    if not directory and not resolved.is_file():
        raise ManifestError(f"{field}: must name an existing file")
    return resolved


def _relative(value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field}: must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ManifestError(f"{field}: must be a relative path without '..'")
    return path


def _scope(value: object, field: str, *, required: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ManifestError(f"{field}: must be an array of relative paths")
    if required and not value:
        raise ManifestError(f"{field}: must list at least one path")
    entries = tuple(_relative(item, field).as_posix() for item in value)
    if len(set(entries)) != len(entries):
        raise ManifestError(f"{field}: must not repeat a path")
    return entries


def _commands(value: object, field: str, baseline: Path) -> tuple[Mapping[str, str], ...]:
    if not isinstance(value, list) or not value:
        raise ManifestError(f"{field}: must list at least one exact command")
    entries: list[Mapping[str, str]] = []
    for index, item in enumerate(value):
        name = f"{field}[{index}]"
        if not isinstance(item, dict) or set(item) != {"command", "cwd"}:
            raise ManifestError(f"{field}: expected only command and cwd fields in {name}")
        command = item["command"]
        cwd = item["cwd"]
        if not isinstance(command, str) or not command.strip():
            raise ManifestError(f"{field}: {name}.command must be a non-empty exact string")
        relative = _commands_cwd(cwd, field, name)
        if not (baseline / relative).is_dir():
            raise ManifestError(f"{field}: {name}.cwd must name a baseline directory")
        entries.append({"command": command, "cwd": relative.as_posix()})
    signatures = {(entry["command"], entry["cwd"]) for entry in entries}
    if len(signatures) != len(entries):
        raise ManifestError(f"{field}: must not repeat a command and cwd pair")
    return tuple(entries)


def _commands_cwd(value: object, field: str, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field}: {name}.cwd must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ManifestError(f"{field}: {name}.cwd must be a relative path without '..'")
    return path


def _is_slug(value: str) -> bool:
    return bool(value) and all(
        character.isascii() and (character.islower() or character.isdigit() or character == "-")
        for character in value
    )


def workspace_scope(paths: Sequence[str], candidate: str) -> bool:
    """Return whether ``candidate`` sits inside one of the declared scope paths."""
    target = Path(candidate)
    return any(target == Path(item) or Path(item) in target.parents for item in paths)


__all__ = [
    "CATEGORIES",
    "EvaluationManifest",
    "IGNORED_NAMES",
    "ManifestError",
    "SCHEMA_VERSION",
    "TaskSpec",
    "content_hash",
    "tree_files",
    "tree_hash",
    "validate_manifest",
    "workspace_scope",
]
