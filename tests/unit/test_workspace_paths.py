from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from coding_agent.tools.paths import WorkspaceBoundary, WorkspacePathError


def test_resolve_accepts_relative_and_absolute_paths_inside_workspace(tmp_path: Path) -> None:
    """A path inside the fixed workspace must remain available in either form."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("print('ok')\n", encoding="utf-8")

    boundary = WorkspaceBoundary(workspace)

    assert boundary.resolve("src/main.py") == target.resolve()
    assert boundary.resolve(str(target)) == target.resolve()


@pytest.mark.parametrize(
    ("path", "code"),
    [("../outside.txt", "PATH_PARENT_TRAVERSAL"), ("/tmp/outside.txt", "PATH_OUTSIDE_WORKSPACE")],
)
def test_resolve_rejects_paths_outside_workspace(tmp_path: Path, path: str, code: str) -> None:
    """Removing the containment check would let a tool escape its session workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    absolute_path = str(outside) if path.startswith("/") else path

    with pytest.raises(WorkspacePathError) as error:
        WorkspaceBoundary(workspace).resolve(absolute_path)

    assert error.value.code == code


def test_resolve_rejects_symlink_that_points_outside_workspace(tmp_path: Path) -> None:
    """Following an external symlink would bypass a lexical path-prefix check."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    (workspace / "escape.txt").symlink_to(outside)

    with pytest.raises(WorkspacePathError, match="outside") as error:
        WorkspaceBoundary(workspace).resolve("escape.txt")

    assert error.value.code == "PATH_OUTSIDE_WORKSPACE"


def test_resolve_rejects_parent_traversal_even_when_it_normalizes_inside_workspace(
    tmp_path: Path,
) -> None:
    """Normalizing ``..`` inside the root would violate the bound path-input policy."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    directory = workspace / "directory"
    directory.mkdir()
    (workspace / "file.txt").write_text("safe", encoding="utf-8")

    with pytest.raises(WorkspacePathError) as error:
        WorkspaceBoundary(workspace).resolve("directory/../file.txt")

    assert error.value.code == "PATH_PARENT_TRAVERSAL"


def test_resolve_translates_cyclic_symlink_to_stable_path_error(tmp_path: Path) -> None:
    """A cyclic link must not make a model-supplied path raise out of the tool boundary."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    loop = workspace / "loop"
    loop.symlink_to(loop)

    with pytest.raises(WorkspacePathError) as error:
        WorkspaceBoundary(workspace).resolve("loop")

    assert error.value.code == "PATH_RESOLUTION_FAILED"


def test_resolve_missing_leaf_uses_existing_canonical_parent(tmp_path: Path) -> None:
    """Resolving a future file through a symlinked directory must preserve containment."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source"
    source.mkdir()
    (workspace / "alias").symlink_to(source, target_is_directory=True)

    resolved = WorkspaceBoundary(workspace).resolve("alias/new.txt", allow_missing_leaf=True)

    assert resolved == source / "new.txt"


def test_workspace_root_is_canonical_and_immutable_after_session_creation(tmp_path: Path) -> None:
    """Allowing the stored root to change would invalidate every prepared tool target."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = tmp_path / "workspace-alias"
    alias.symlink_to(workspace, target_is_directory=True)
    boundary = WorkspaceBoundary(alias)

    assert boundary.root == workspace.resolve()
    with pytest.raises(FrozenInstanceError):
        boundary.root = tmp_path  # type: ignore[misc]
