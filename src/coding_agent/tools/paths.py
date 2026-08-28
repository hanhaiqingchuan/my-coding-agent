"""Canonical workspace containment checks for local file tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class WorkspacePathError(ValueError):
    """A recoverable failure to resolve a target within its workspace."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class WorkspaceBoundary:
    """The canonical, immutable root attached to one session."""

    root: Path

    def __post_init__(self) -> None:
        try:
            root = self.root.resolve(strict=True)
        except FileNotFoundError as error:
            raise WorkspacePathError(
                "WORKSPACE_NOT_FOUND", "workspace directory does not exist"
            ) from error
        except (OSError, RuntimeError) as error:
            raise WorkspacePathError(
                "WORKSPACE_RESOLUTION_FAILED", "workspace path could not be resolved"
            ) from error
        if not root.is_dir():
            raise WorkspacePathError("WORKSPACE_NOT_DIRECTORY", "workspace must be a directory")
        object.__setattr__(self, "root", root)

    def resolve(self, path: str, allow_missing_leaf: bool = False) -> Path:
        """Resolve a user path and require its canonical target to remain under ``root``.

        P0 deliberately re-checks this boundary immediately before each I/O operation;
        it does not claim protection against a malicious concurrent directory swap.
        """
        if not isinstance(path, str) or not path:
            raise WorkspacePathError("INVALID_PATH", "path must be a non-empty string")
        candidate = Path(path)
        if ".." in candidate.parts:
            raise WorkspacePathError(
                "PATH_PARENT_TRAVERSAL", "path must not contain parent traversal"
            )
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            try:
                unresolved = candidate.resolve(strict=False)
            except (OSError, RuntimeError) as resolution_error:
                raise WorkspacePathError(
                    "PATH_RESOLUTION_FAILED", "path could not be resolved"
                ) from resolution_error
            self._require_inside(unresolved)
            if not allow_missing_leaf:
                raise WorkspacePathError("PATH_NOT_FOUND", "path does not exist") from error
            try:
                parent = candidate.parent.resolve(strict=True)
            except FileNotFoundError as parent_error:
                raise WorkspacePathError(
                    "PATH_PARENT_NOT_FOUND", "path parent directory does not exist"
                ) from parent_error
            except (OSError, RuntimeError) as resolution_error:
                raise WorkspacePathError(
                    "PATH_RESOLUTION_FAILED", "path could not be resolved"
                ) from resolution_error
            self._require_inside(parent)
            resolved = parent / candidate.name
        except (OSError, RuntimeError) as error:
            raise WorkspacePathError(
                "PATH_RESOLUTION_FAILED", "path could not be resolved"
            ) from error
        self._require_inside(resolved)
        return resolved

    def _require_inside(self, path: Path) -> None:
        if not path.is_relative_to(self.root):
            raise WorkspacePathError("PATH_OUTSIDE_WORKSPACE", "path is outside the workspace")
