"""Pure construction of bounded, provider-neutral model context views."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypeAlias

from coding_agent.context.estimator import ESTIMATOR_ID, estimate_input_tokens
from coding_agent.core.models import (
    ContextSnapshot,
    Message,
    MessageStatus,
    TextPart,
    ToolResult,
    ToolUsePart,
)
from coding_agent.model.protocol import ModelMessage


@dataclass(frozen=True, slots=True)
class ContextRequest:
    system: str
    context_window: int
    max_output_tokens: int
    safety_margin_tokens: int
    compact_trigger_ratio: float = 0.80
    compact_target_ratio: float = 0.60
    summary_max_tokens: int = 1_024
    recent_user_turns: int = 2
    current_run_id: str | None = None
    tool_schemas: tuple[Mapping[str, object], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.system, str):
            raise TypeError("context system must be a string")
        for name, value in (
            ("context_window", self.context_window),
            ("max_output_tokens", self.max_output_tokens),
            ("safety_margin_tokens", self.safety_margin_tokens),
            ("summary_max_tokens", self.summary_max_tokens),
            ("recent_user_turns", self.recent_user_turns),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.context_window <= self.max_output_tokens + self.safety_margin_tokens:
            raise ValueError("context window must exceed output budget plus safety margin")
        if self.summary_max_tokens == 0:
            raise ValueError("summary_max_tokens must be positive")
        if self.recent_user_turns < 2:
            raise ValueError("recent_user_turns must be at least 2")
        if not 0 < self.compact_target_ratio <= self.compact_trigger_ratio <= 1:
            raise ValueError("compaction ratios must satisfy 0 < target <= trigger <= 1")
        object.__setattr__(self, "tool_schemas", tuple(self.tool_schemas))

    @property
    def available_input_tokens(self) -> int:
        return self.context_window - self.max_output_tokens - self.safety_margin_tokens


@dataclass(frozen=True, slots=True)
class ContextView:
    system: str
    messages: tuple[ModelMessage, ...]
    tool_schemas: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class CompactionCandidate:
    """A complete replaceable assistant or assistant/tool group."""

    messages: tuple[Message, ...]
    read_only_user_context: tuple[Message, ...]
    source_message_seqs: tuple[int, ...]
    source_event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompactionPlan:
    candidates: tuple[CompactionCandidate, ...]
    previous_snapshot: ContextSnapshot | None
    source_message_seqs: tuple[int, ...]
    source_event_ids: tuple[str, ...]
    current_estimate_tokens: int
    retained_estimate_tokens: int
    soft_target_tokens: int
    target_tokens: int
    required_reduction_tokens: int
    available_tokens: int
    summary_max_tokens: int
    compaction_input_budget_tokens: int
    compaction_above_target: bool
    estimator_id: str = ESTIMATOR_ID


@dataclass(frozen=True, slots=True)
class ReadyContext:
    view: ContextView
    estimated_tokens: int
    available_tokens: int
    trigger_tokens: int
    target_tokens: int
    mandatory_tokens: int
    mandatory_user_tokens: int
    pruned_bytes: int
    compaction_above_target: bool
    estimator_id: str = ESTIMATOR_ID


@dataclass(frozen=True, slots=True)
class CompactionRequired:
    view: ContextView
    estimated_tokens: int
    available_tokens: int
    trigger_tokens: int
    target_tokens: int
    mandatory_tokens: int
    mandatory_user_tokens: int
    pruned_bytes: int
    plan: CompactionPlan
    estimator_id: str = ESTIMATOR_ID


@dataclass(frozen=True, slots=True)
class ContextOverflow:
    required_tokens: int
    available_tokens: int
    mandatory_tokens: int
    mandatory_user_tokens: int
    trigger_tokens: int
    target_tokens: int
    diagnostic: Mapping[str, str | int | float]
    code: str = "CONTEXT_OVERFLOW"
    estimator_id: str = ESTIMATOR_ID


ContextBuildResult: TypeAlias = ReadyContext | CompactionRequired | ContextOverflow


@dataclass(frozen=True, slots=True)
class _CanonicalGroup:
    kind: Literal["user", "assistant", "tool_exchange"]
    messages: tuple[Message, ...]
    model_messages: tuple[ModelMessage, ...]
    read_only_user_context: tuple[Message, ...] = ()

    @property
    def first_seq(self) -> int:
        return self.messages[0].seq

    @property
    def last_seq(self) -> int:
        return self.messages[-1].seq


class ContextBuilder:
    """Build a model view without changing the canonical transcript or storage."""

    def build(
        self,
        transcript: Sequence[Message],
        snapshot: ContextSnapshot | None,
        request: ContextRequest,
    ) -> ContextBuildResult:
        committed = tuple(
            sorted(
                (message for message in transcript if message.status is MessageStatus.COMMITTED),
                key=lambda message: message.seq,
            )
        )
        _require_unique_sequence_numbers(committed)
        groups = _group_committed_messages(committed)
        recent_completed_indexes, recent_cutoff = _recent_completed_rounds(
            groups,
            request.recent_user_turns,
            request.current_run_id,
        )
        mandatory_indexes = {
            index
            for index, group in enumerate(groups)
            if _is_mandatory(
                group,
                index,
                recent_completed_indexes,
                request.current_run_id,
                snapshot,
            )
        }

        mandatory_messages = _flatten_model_messages(
            group for index, group in enumerate(groups) if index in mandatory_indexes
        )
        mandatory_user_messages = _flatten_model_messages(
            group for group in groups if group.kind == "user"
        )
        mandatory_tokens = estimate_input_tokens(
            request.system,
            mandatory_messages,
            request.tool_schemas,
        )
        empty_request_tokens = estimate_input_tokens("", (), ())
        mandatory_user_tokens = max(
            0,
            estimate_input_tokens("", mandatory_user_messages, ()) - empty_request_tokens,
        )
        available = request.available_input_tokens
        trigger_tokens = math.ceil(available * request.compact_trigger_ratio)
        soft_target_tokens = math.ceil(available * request.compact_target_ratio)
        target_tokens = max(soft_target_tokens, mandatory_tokens)
        compaction_above_target = mandatory_tokens > soft_target_tokens

        if mandatory_tokens > available:
            diagnostic = MappingProxyType(
                {
                    "reason": "mandatory_content_exceeds_available_input",
                    "context_window": request.context_window,
                    "max_output_tokens": request.max_output_tokens,
                    "safety_margin_tokens": request.safety_margin_tokens,
                    "recent_user_turns": request.recent_user_turns,
                }
            )
            return ContextOverflow(
                required_tokens=mandatory_tokens,
                available_tokens=available,
                mandatory_tokens=mandatory_tokens,
                mandatory_user_tokens=mandatory_user_tokens,
                trigger_tokens=trigger_tokens,
                target_tokens=target_tokens,
                diagnostic=diagnostic,
            )

        selected = tuple(
            (index, group)
            for index, group in enumerate(groups)
            if _is_visible(group, index, mandatory_indexes, snapshot)
        )
        replacements: dict[int, tuple[ModelMessage, ...]] = {}
        view = _make_view(selected, replacements, snapshot, recent_cutoff, request)
        estimated = estimate_input_tokens(view.system, view.messages, view.tool_schemas)

        if estimated < trigger_tokens:
            return ReadyContext(
                view=view,
                estimated_tokens=estimated,
                available_tokens=available,
                trigger_tokens=trigger_tokens,
                target_tokens=target_tokens,
                mandatory_tokens=mandatory_tokens,
                mandatory_user_tokens=mandatory_user_tokens,
                pruned_bytes=0,
                compaction_above_target=compaction_above_target,
            )

        pruned_bytes = 0
        for index, group in selected:
            if index in mandatory_indexes or group.kind != "tool_exchange":
                continue
            replacement, saved_bytes = _prune_tool_group(group)
            if saved_bytes <= 0:
                continue
            replacements[index] = replacement
            pruned_bytes += saved_bytes
            view = _make_view(selected, replacements, snapshot, recent_cutoff, request)
            estimated = estimate_input_tokens(view.system, view.messages, view.tool_schemas)
            if estimated <= target_tokens:
                break

        if estimated <= target_tokens:
            return ReadyContext(
                view=view,
                estimated_tokens=estimated,
                available_tokens=available,
                trigger_tokens=trigger_tokens,
                target_tokens=target_tokens,
                mandatory_tokens=mandatory_tokens,
                mandatory_user_tokens=mandatory_user_tokens,
                pruned_bytes=pruned_bytes,
                compaction_above_target=compaction_above_target,
            )

        candidates = tuple(
            _candidate(group)
            for index, group in selected
            if index not in mandatory_indexes and group.kind != "user"
        )
        source_message_seqs = tuple(
            seq for candidate in candidates for seq in candidate.source_message_seqs
        )
        source_event_ids = tuple(
            event_id for candidate in candidates for event_id in candidate.source_event_ids
        )
        compaction_input_budget = max(
            0,
            request.context_window
            - request.summary_max_tokens
            - request.safety_margin_tokens
            - estimate_input_tokens("", (), ()),
        )
        plan = CompactionPlan(
            candidates=candidates,
            previous_snapshot=snapshot,
            source_message_seqs=source_message_seqs,
            source_event_ids=source_event_ids,
            current_estimate_tokens=estimated,
            retained_estimate_tokens=mandatory_tokens,
            soft_target_tokens=soft_target_tokens,
            target_tokens=target_tokens,
            required_reduction_tokens=max(0, estimated - target_tokens),
            available_tokens=available,
            summary_max_tokens=request.summary_max_tokens,
            compaction_input_budget_tokens=compaction_input_budget,
            compaction_above_target=compaction_above_target,
        )
        return CompactionRequired(
            view=view,
            estimated_tokens=estimated,
            available_tokens=available,
            trigger_tokens=trigger_tokens,
            target_tokens=target_tokens,
            mandatory_tokens=mandatory_tokens,
            mandatory_user_tokens=mandatory_user_tokens,
            pruned_bytes=pruned_bytes,
            plan=plan,
        )


def _require_unique_sequence_numbers(messages: Sequence[Message]) -> None:
    seqs = [message.seq for message in messages]
    if len(seqs) != len(set(seqs)):
        raise ValueError("committed transcript message sequence numbers must be unique")


def _group_committed_messages(messages: Sequence[Message]) -> tuple[_CanonicalGroup, ...]:
    groups: list[_CanonicalGroup] = []
    current_user: Message | None = None
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == "user":
            model_message = ModelMessage("user", message.parts)
            current_user = message
            groups.append(_CanonicalGroup("user", (message,), (model_message,)))
            index += 1
            continue
        if message.role == "tool":
            raise ValueError("committed tool result has no preceding assistant tool use")
        if message.role != "assistant":
            raise ValueError(f"unsupported canonical message role: {message.role!r}")

        assistant_model = ModelMessage("assistant", message.parts)
        expected_ids = tuple(
            part.call.id for part in message.parts if isinstance(part, ToolUsePart)
        )
        if not expected_ids:
            groups.append(
                _CanonicalGroup(
                    "assistant",
                    (message,),
                    (assistant_model,),
                    (current_user,) if current_user else (),
                )
            )
            index += 1
            continue

        tool_messages: list[Message] = []
        results: list[ToolResult] = []
        next_index = index + 1
        while next_index < len(messages) and messages[next_index].role == "tool":
            tool_message = messages[next_index]
            tool_results = [part for part in tool_message.parts if isinstance(part, ToolResult)]
            if len(tool_results) != len(tool_message.parts):
                raise ValueError("canonical tool messages may only contain tool results")
            tool_messages.append(tool_message)
            results.extend(tool_results)
            next_index += 1
            if len(results) >= len(expected_ids):
                break
        result_ids = tuple(result.tool_call_id for result in results)
        if result_ids != expected_ids:
            raise ValueError("committed assistant tool use requires all ordered tool results")
        result_model = ModelMessage("user", tuple(results))
        groups.append(
            _CanonicalGroup(
                "tool_exchange",
                (message, *tool_messages),
                (assistant_model, result_model),
                (current_user,) if current_user else (),
            )
        )
        index = next_index
    return tuple(groups)


def _recent_completed_rounds(
    groups: Sequence[_CanonicalGroup],
    count: int,
    current_run_id: str | None,
) -> tuple[frozenset[int], int]:
    completed_user_indexes = [
        index
        for index, group in enumerate(groups)
        if group.kind == "user"
        and (current_run_id is None or group.messages[0].run_id != current_run_id)
    ]
    selected_starts = completed_user_indexes[-count:]
    selected_indexes: set[int] = set()
    for start in selected_starts:
        end = next(
            (
                index
                for index in range(start + 1, len(groups))
                if groups[index].kind == "user"
            ),
            len(groups),
        )
        selected_indexes.update(range(start, end))

    if selected_starts:
        active_zone_cutoff = groups[selected_starts[0]].first_seq
    else:
        active_zone_cutoff = next(
            (
                group.first_seq
                for group in groups
                if group.kind == "user"
                and current_run_id is not None
                and group.messages[0].run_id == current_run_id
            ),
            max((group.last_seq for group in groups), default=-1) + 1,
        )
    return frozenset(selected_indexes), active_zone_cutoff


def _is_mandatory(
    group: _CanonicalGroup,
    index: int,
    recent_completed_indexes: frozenset[int],
    current_run_id: str | None,
    snapshot: ContextSnapshot | None,
) -> bool:
    if group.kind == "user" or index in recent_completed_indexes:
        return True
    return bool(
        group.kind == "tool_exchange"
        and current_run_id is not None
        and group.messages[0].run_id == current_run_id
        and (snapshot is None or group.last_seq > snapshot.covered_through_message_seq)
    )


def _is_visible(
    group: _CanonicalGroup,
    index: int,
    mandatory_indexes: set[int],
    snapshot: ContextSnapshot | None,
) -> bool:
    if index in mandatory_indexes or snapshot is None:
        return True
    return group.last_seq > snapshot.covered_through_message_seq


def _flatten_model_messages(groups: Iterable[_CanonicalGroup]) -> tuple[ModelMessage, ...]:
    return tuple(message for group in groups for message in group.model_messages)


def _make_view(
    selected: Sequence[tuple[int, _CanonicalGroup]],
    replacements: Mapping[int, tuple[ModelMessage, ...]],
    snapshot: ContextSnapshot | None,
    recent_cutoff: int,
    request: ContextRequest,
) -> ContextView:
    prefix: list[ModelMessage] = []
    suffix: list[ModelMessage] = []
    for index, group in selected:
        projected = replacements.get(index, group.model_messages)
        is_old_user = bool(
            snapshot is not None
            and group.kind == "user"
            and group.last_seq <= snapshot.covered_through_message_seq
            and group.first_seq < recent_cutoff
        )
        (prefix if is_old_user else suffix).extend(projected)
    if snapshot is not None and snapshot.summary:
        prefix.append(ModelMessage("assistant", (TextPart(snapshot.summary),)))
    return ContextView(
        system=request.system,
        messages=tuple(prefix + suffix),
        tool_schemas=request.tool_schemas,
    )


def _prune_tool_group(group: _CanonicalGroup) -> tuple[tuple[ModelMessage, ...], int]:
    calls = {
        part.call.id: part.call
        for part in group.messages[0].parts
        if isinstance(part, ToolUsePart)
    }
    replacement_results: list[ToolResult] = []
    saved_bytes = 0
    for message in group.messages[1:]:
        for part in message.parts:
            if not isinstance(part, ToolResult):  # pragma: no cover - grouping validates this
                continue
            call = calls[part.tool_call_id]
            content_bytes = len(part.content.encode("utf-8"))
            status = "succeeded" if part.ok else "failed"
            target = _tool_target(call.input)
            placeholder = (
                "[tool output pruned: "
                f"tool={call.name} status={status} target={target} "
                f"original_bytes={content_bytes} "
                f"original_truncated={str(part.truncated).lower()} context_pruned=true]"
            )
            replacement_size = len(placeholder.encode("utf-8"))
            if replacement_size >= content_bytes:
                replacement_results.append(part)
                continue
            saved_bytes += content_bytes - replacement_size
            replacement_results.append(
                ToolResult(
                    tool_call_id=part.tool_call_id,
                    content=placeholder,
                    ok=part.ok,
                    error=part.error,
                    truncated=True,
                )
            )
    if saved_bytes <= 0:
        return group.model_messages, 0
    return (
        group.model_messages[0],
        ModelMessage("user", tuple(replacement_results)),
    ), saved_bytes


def _tool_target(tool_input: Mapping[str, object]) -> str:
    for key in ("path", "cwd", "command"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return " ".join(value.splitlines())
    return "-"


def _candidate(group: _CanonicalGroup) -> CompactionCandidate:
    return CompactionCandidate(
        messages=group.messages,
        read_only_user_context=group.read_only_user_context,
        source_message_seqs=tuple(message.seq for message in group.messages),
        source_event_ids=tuple(message.id for message in group.messages),
    )


__all__ = [
    "CompactionCandidate",
    "CompactionPlan",
    "CompactionRequired",
    "ContextBuildResult",
    "ContextBuilder",
    "ContextOverflow",
    "ContextRequest",
    "ContextView",
    "ReadyContext",
]
