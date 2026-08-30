"""Local tool contracts and result helpers."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.models import ToolError, ToolResult

OutputSink = Callable[[str], Awaitable[None]]


class ToolInputError(ValueError):
    """A model-correctable tool argument error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Runtime dependencies shared by all local tools."""

    workspace: "WorkspaceBoundary"
    cancellation: CancellationToken
    emit_output: OutputSink
    content_fingerprints: Mapping[str, str] = MappingProxyType({})
    """Absolute target path -> sha256 of the bytes this session last read or wrote.

    The loop owns this per-session view so ``write_file`` can refuse to execute
    against content the model has never seen in its current form (spec 10.3's
    freshness gate). An empty mapping means no session reads are known.
    """


def result(
    tool_call_id: str,
    tool: str,
    *,
    ok: bool,
    summary: str,
    data: Mapping[str, object] | None = None,
    error: ToolError | None = None,
    truncated: bool = False,
    duration_ms: int = 0,
) -> ToolResult:
    """Build the stable model-visible envelope and its typed core representation."""
    body = {
        "ok": ok,
        "tool": tool,
        "summary": summary,
        "data": dict(data or {}),
        "error": {"code": error.code, "message": error.message} if error else None,
        "truncated": truncated,
        "duration_ms": duration_ms,
    }
    return ToolResult(
        tool_call_id=tool_call_id,
        content=json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        ok=ok,
        error=error,
        data=body["data"],
        truncated=truncated,
    )


def error_result(tool_call_id: str, tool: str, code: str, message: str) -> ToolResult:
    """Return a stable failed result instead of escalating a recoverable call."""
    return result(
        tool_call_id,
        tool,
        ok=False,
        summary=message,
        error=ToolError(code, message),
    )


from coding_agent.tools.paths import WorkspaceBoundary  # noqa: E402

__all__ = ["OutputSink", "ToolContext", "ToolInputError", "error_result", "result"]
