"""Synchronous, loss-aware context compaction."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import Literal

from coding_agent.context.builder import CompactionCandidate, CompactionPlan
from coding_agent.context.estimator import estimate_input_tokens
from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.errors import CancellationRequested, StoreError
from coding_agent.core.models import (
    ContextSnapshot,
    Message,
    ModelStopReason,
    TextPart,
    ToolResult,
    ToolUsePart,
)
from coding_agent.model.protocol import (
    ModelAPIError,
    ModelGateway,
    ModelMessage,
    ModelProtocolError,
    ModelRequest,
    ModelTransportError,
    TextDelta,
)
from coding_agent.storage.sqlite import SQLiteStore

CompressionPhase = Literal["planning", "request", "validation", "persistence"]
_SUMMARY_FIELDS = (
    "completed_work_and_evidence",
    "important_files_and_symbols",
    "tool_findings",
    "commands_and_tests",
    "failed_attempts",
    "remaining_work",
    "blockers",
    "next_steps",
)


@dataclass(frozen=True, slots=True)
class CompressionError:
    phase: CompressionPhase
    code: str
    required_tokens: int
    available_tokens: int
    retryable: bool

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("compression error code must not be empty")
        if self.required_tokens < 0 or self.available_tokens < 0:
            raise ValueError("compression error token counts must not be negative")


@dataclass(frozen=True, slots=True)
class CompactionResult:
    snapshot: ContextSnapshot | None
    error: CompressionError | None

    def __post_init__(self) -> None:
        if (self.snapshot is None) == (self.error is None):
            raise ValueError("compaction result requires exactly one of snapshot or error")


class Compactor:
    def __init__(self, gateway: ModelGateway, store: SQLiteStore, *, model: str) -> None:
        self._gateway = gateway
        self._store = store
        self._model = model

    async def compact(
        self,
        plan: CompactionPlan,
        cancellation: CancellationToken,
    ) -> CompactionResult:
        try:
            return await self._compact(plan, cancellation)
        except CancellationRequested:
            return _failure(
                phase="request",
                code="COMPRESSION_CANCELLED",
                available_tokens=plan.compaction_input_budget_tokens,
            )

    async def _compact(
        self,
        plan: CompactionPlan,
        cancellation: CancellationToken,
    ) -> CompactionResult:
        planning_error = _validate_plan(plan)
        if planning_error is not None:
            return planning_error

        cancellation.raise_if_cancelled()
        system = files("coding_agent.prompts").joinpath("compact.md").read_text(encoding="utf-8")
        rolling_summary = plan.previous_snapshot.summary if plan.previous_snapshot else ""
        summary_token_estimate = 0
        next_candidate = 0

        while next_candidate < len(plan.candidates):
            request, chunk_end, required_tokens = _next_request(
                system=system,
                rolling_summary=rolling_summary,
                candidates=plan.candidates,
                start=next_candidate,
                max_tokens=plan.summary_max_tokens,
                input_budget=plan.compaction_input_budget_tokens,
            )
            if request is None:
                return _failure(
                    phase="planning",
                    code="COMPACTION_INPUT_OVERFLOW",
                    required_tokens=required_tokens,
                    available_tokens=plan.compaction_input_budget_tokens,
                )

            cancellation.raise_if_cancelled()
            try:
                turn = await self._gateway.complete(request, _ignore_delta, cancellation)
            except ModelAPIError as error:
                return _failure(
                    phase="request",
                    code="MODEL_API_ERROR",
                    available_tokens=plan.compaction_input_budget_tokens,
                    retryable=error.retryable,
                )
            except ModelTransportError as error:
                return _failure(
                    phase="request",
                    code="MODEL_TRANSPORT_ERROR",
                    available_tokens=plan.compaction_input_budget_tokens,
                    retryable=error.retryable,
                )
            except ModelProtocolError:
                return _failure(
                    phase="request",
                    code="MODEL_PROTOCOL_ERROR",
                    available_tokens=plan.compaction_input_budget_tokens,
                )

            validation = _validated_summary(
                turn.parts,
                turn.stop_reason,
                plan.summary_max_tokens,
            )
            if isinstance(validation, CompressionError):
                return CompactionResult(snapshot=None, error=validation)
            rolling_summary, summary_token_estimate = validation
            next_candidate = chunk_end

        cancellation.raise_if_cancelled()
        effect_error = _validate_compaction_effect(plan, summary_token_estimate)
        if effect_error is not None:
            return effect_error
        mandatory_floor_active = plan.retained_estimate_tokens > plan.soft_target_tokens
        snapshot = _new_snapshot(
            plan,
            rolling_summary,
            summary_token_estimate,
            self._model,
            compaction_above_target=mandatory_floor_active,
        )
        try:
            self._store.replace_context_snapshot(snapshot)
        except (sqlite3.Error, StoreError):
            return _failure(
                phase="persistence",
                code="SNAPSHOT_PERSISTENCE_ERROR",
                required_tokens=summary_token_estimate,
                available_tokens=plan.summary_max_tokens,
            )
        return CompactionResult(snapshot=snapshot, error=None)


def _validate_plan(plan: CompactionPlan) -> CompactionResult | None:
    if not plan.candidates:
        return _failure(
            phase="planning",
            code="NO_COMPACTION_CANDIDATES",
            available_tokens=plan.compaction_input_budget_tokens,
        )
    if plan.compaction_input_budget_tokens <= 0:
        return _failure(
            phase="planning",
            code="COMPACTION_INPUT_OVERFLOW",
            required_tokens=1,
            available_tokens=max(0, plan.compaction_input_budget_tokens),
        )
    if (
        min(
            plan.current_estimate_tokens,
            plan.retained_estimate_tokens,
            plan.soft_target_tokens,
            plan.target_tokens,
            plan.required_reduction_tokens,
            plan.available_tokens,
        )
        < 0
        or plan.target_tokens != max(plan.soft_target_tokens, plan.retained_estimate_tokens)
        or plan.required_reduction_tokens
        != max(0, plan.current_estimate_tokens - plan.target_tokens)
        or plan.target_tokens > plan.available_tokens
    ):
        return _failure(
            phase="planning",
            code="INVALID_COMPACTION_PLAN",
            available_tokens=plan.compaction_input_budget_tokens,
        )

    source_seqs = tuple(
        seq for candidate in plan.candidates for seq in candidate.source_message_seqs
    )
    source_ids = tuple(
        event_id for candidate in plan.candidates for event_id in candidate.source_event_ids
    )
    if source_seqs != plan.source_message_seqs or source_ids != plan.source_event_ids:
        return _failure(
            phase="planning",
            code="INVALID_COMPACTION_PLAN",
            available_tokens=plan.compaction_input_budget_tokens,
        )
    if len(source_ids) != len(set(source_ids)):
        return _failure(
            phase="planning",
            code="INVALID_COMPACTION_PLAN",
            available_tokens=plan.compaction_input_budget_tokens,
        )
    if any(not _is_complete_candidate(candidate) for candidate in plan.candidates):
        return _failure(
            phase="planning",
            code="INVALID_COMPACTION_PLAN",
            available_tokens=plan.compaction_input_budget_tokens,
        )

    session_ids = {
        message.session_id
        for candidate in plan.candidates
        for message in (*candidate.messages, *candidate.read_only_user_context)
    }
    if len(session_ids) != 1:
        return _failure(
            phase="planning",
            code="INVALID_COMPACTION_PLAN",
            available_tokens=plan.compaction_input_budget_tokens,
        )
    if plan.previous_snapshot is not None and plan.previous_snapshot.session_id not in session_ids:
        return _failure(
            phase="planning",
            code="INVALID_COMPACTION_PLAN",
            available_tokens=plan.compaction_input_budget_tokens,
        )
    return None


def _validate_compaction_effect(
    plan: CompactionPlan,
    summary_token_estimate: int,
) -> CompactionResult | None:
    final_estimate = plan.retained_estimate_tokens + summary_token_estimate
    if final_estimate > plan.available_tokens:
        return _failure(
            phase="validation",
            code="COMPACTION_RESULT_OVERFLOW",
            required_tokens=final_estimate,
            available_tokens=plan.available_tokens,
        )

    mandatory_floor_active = plan.retained_estimate_tokens > plan.soft_target_tokens
    actual_reduction = max(0, plan.current_estimate_tokens - final_estimate)
    if not mandatory_floor_active and (
        final_estimate > plan.target_tokens or actual_reduction < plan.required_reduction_tokens
    ):
        return _failure(
            phase="validation",
            code="INSUFFICIENT_COMPRESSION",
            required_tokens=plan.required_reduction_tokens,
            available_tokens=actual_reduction,
        )
    return None


def _is_complete_candidate(candidate: CompactionCandidate) -> bool:
    if not candidate.messages or candidate.messages[0].role != "assistant":
        return False
    if any(message.role != "tool" for message in candidate.messages[1:]):
        return False
    if any(message.role != "user" for message in candidate.read_only_user_context):
        return False
    if candidate.source_message_seqs != tuple(message.seq for message in candidate.messages):
        return False
    if candidate.source_event_ids != tuple(message.id for message in candidate.messages):
        return False

    assistant = candidate.messages[0]
    if any(not isinstance(part, TextPart | ToolUsePart) for part in assistant.parts):
        return False
    tool_call_ids = tuple(part.call.id for part in assistant.parts if isinstance(part, ToolUsePart))
    results: list[ToolResult] = []
    for message in candidate.messages[1:]:
        if any(not isinstance(part, ToolResult) for part in message.parts):
            return False
        results.extend(part for part in message.parts if isinstance(part, ToolResult))
    result_ids = tuple(result.tool_call_id for result in results)
    return result_ids == tool_call_ids and bool(assistant.parts)


def _next_request(
    *,
    system: str,
    rolling_summary: str,
    candidates: tuple[CompactionCandidate, ...],
    start: int,
    max_tokens: int,
    input_budget: int,
) -> tuple[ModelRequest | None, int, int]:
    selected: list[CompactionCandidate] = []
    best_request: ModelRequest | None = None
    best_end = start
    required_tokens = 0
    for index in range(start, len(candidates)):
        selected.append(candidates[index])
        request = _make_request(system, rolling_summary, tuple(selected), max_tokens)
        required_tokens = estimate_input_tokens(request.system, request.messages, ())
        if required_tokens > input_budget:
            break
        best_request = request
        best_end = index + 1
    return best_request, best_end, required_tokens


def _make_request(
    system: str,
    rolling_summary: str,
    candidates: tuple[CompactionCandidate, ...],
    max_tokens: int,
) -> ModelRequest:
    payload = _chunk_payload(candidates, rolling_summary)
    return ModelRequest(
        system=system,
        messages=(ModelMessage("user", (TextPart(payload),)),),
        tools=(),
        max_tokens=max_tokens,
    )


def _chunk_payload(
    candidates: tuple[CompactionCandidate, ...],
    previous_summary: str,
) -> str:
    read_only_context: list[dict[str, object]] = []
    seen_user_ids: set[str] = set()
    for candidate in candidates:
        for message in candidate.read_only_user_context:
            if message.id in seen_user_ids:
                continue
            seen_user_ids.add(message.id)
            read_only_context.append(_message_value(message))
    value = {
        "previous_summary": previous_summary or None,
        "read_only_user_context": read_only_context,
        "replaceable_groups": [
            {
                "source_event_ids": list(candidate.source_event_ids),
                "messages": [_message_value(message) for message in candidate.messages],
            }
            for candidate in candidates
        ],
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _message_value(message: Message) -> dict[str, object]:
    return {
        "event_id": message.id,
        "role": message.role,
        "parts": [_part_value(part) for part in message.parts],
    }


def _part_value(part: TextPart | ToolUsePart | ToolResult) -> dict[str, object]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ToolUsePart):
        return {
            "type": "tool_use",
            "id": part.call.id,
            "name": part.call.name,
            "input": _jsonable(part.call.input),
        }
    if isinstance(part, ToolResult):
        return {
            "type": "tool_result",
            "tool_call_id": part.tool_call_id,
            "content": part.content,
            "ok": part.ok,
            "error": (
                {"code": part.error.code, "message": part.error.message}
                if part.error is not None
                else None
            ),
            "data": _jsonable(part.data),
            "truncated": part.truncated,
        }
    raise TypeError(f"unsupported compaction message part: {type(part).__name__}")


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value


def _validated_summary(
    parts: tuple[TextPart | ToolUsePart, ...],
    stop_reason: ModelStopReason,
    max_tokens: int,
) -> tuple[str, int] | CompressionError:
    if stop_reason is ModelStopReason.MAX_TOKENS:
        return CompressionError("validation", "SUMMARY_TRUNCATED", 0, max_tokens, False)
    if stop_reason not in {ModelStopReason.END_TURN, ModelStopReason.STOP_SEQUENCE}:
        return CompressionError("validation", "INVALID_SUMMARY_STOP_REASON", 0, max_tokens, False)
    if any(isinstance(part, ToolUsePart) for part in parts):
        return CompressionError("validation", "INVALID_SUMMARY_STRUCTURE", 0, max_tokens, False)
    text = "".join(part.text for part in parts if isinstance(part, TextPart)).strip()
    if not text:
        return CompressionError("validation", "EMPTY_SUMMARY", 0, max_tokens, False)
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return CompressionError("validation", "INVALID_SUMMARY_STRUCTURE", 0, max_tokens, False)
    if not isinstance(value, dict) or tuple(sorted(value)) != tuple(sorted(_SUMMARY_FIELDS)):
        return CompressionError("validation", "INVALID_SUMMARY_STRUCTURE", 0, max_tokens, False)
    if any(
        not isinstance(value[field], list)
        or any(not isinstance(item, str) or not item.strip() for item in value[field])
        for field in _SUMMARY_FIELDS
    ):
        return CompressionError("validation", "INVALID_SUMMARY_STRUCTURE", 0, max_tokens, False)
    if not any(value[field] for field in _SUMMARY_FIELDS):
        return CompressionError("validation", "EMPTY_SUMMARY", 0, max_tokens, False)

    normalized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    estimate = _summary_token_estimate(normalized)
    if estimate > max_tokens:
        return CompressionError(
            "validation", "SUMMARY_BUDGET_EXCEEDED", estimate, max_tokens, False
        )
    return normalized, estimate


def _summary_token_estimate(summary: str) -> int:
    empty = estimate_input_tokens("", (), ())
    with_summary = estimate_input_tokens("", (ModelMessage("assistant", (TextPart(summary),)),), ())
    return with_summary - empty


def _new_snapshot(
    plan: CompactionPlan,
    summary: str,
    token_estimate: int,
    model: str,
    *,
    compaction_above_target: bool,
) -> ContextSnapshot:
    previous = plan.previous_snapshot
    session_id = (
        previous.session_id if previous is not None else plan.candidates[0].messages[0].session_id
    )
    previous_sources = previous.source_event_ids if previous is not None else ()
    source_event_ids = tuple(dict.fromkeys((*previous_sources, *plan.source_event_ids)))
    return ContextSnapshot(
        session_id=session_id,
        covered_through_message_seq=max(
            previous.covered_through_message_seq if previous is not None else 0,
            *plan.source_message_seqs,
        ),
        summary=summary,
        created_at=datetime.now(UTC),
        version=(previous.version + 1) if previous is not None else 1,
        source_event_ids=source_event_ids,
        model=model,
        estimator_id=plan.estimator_id,
        token_estimate=token_estimate,
        compaction_above_target=compaction_above_target,
    )


async def _ignore_delta(_: TextDelta) -> None:
    return None


def _failure(
    *,
    phase: CompressionPhase,
    code: str,
    required_tokens: int = 0,
    available_tokens: int,
    retryable: bool = False,
) -> CompactionResult:
    return CompactionResult(
        snapshot=None,
        error=CompressionError(
            phase=phase,
            code=code,
            required_tokens=required_tokens,
            available_tokens=available_tokens,
            retryable=retryable,
        ),
    )


__all__ = ["CompactionResult", "Compactor", "CompressionError", "CompressionPhase"]
