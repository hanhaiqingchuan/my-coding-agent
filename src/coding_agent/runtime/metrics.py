"""Request metrics, canonical hashing, and the read-only run report projection.

The run report is a projection of facts SQLite already owns. Nothing here mutates
state, and nothing here exports prompt text, tool arguments, command output, or
absolute paths: only counts, hashes, token usage, durations and the model identity
the run's own configuration snapshot records — never the credential or the endpoint.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime

from coding_agent.config import ModelSettings
from coding_agent.core.models import Run
from coding_agent.model.protocol import ModelRequest
from coding_agent.storage.sqlite import SQLiteStore

RUN_REPORT_SCHEMA_VERSION = "run-report-v1"
_USAGE_COMPONENTS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
_TOOL_STATES = (
    "succeeded",
    "failed",
    "rejected",
    "cancelled",
    "skipped",
    "unknown",
)


def canonical_json(value: object) -> str:
    """Serialize a value as canonical JSON with sorted keys and no whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(value: object) -> str:
    """Hash a value over its canonical, key-sorted JSON encoding."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def model_config_hash(settings: ModelSettings, request: ModelRequest) -> str:
    """Hash only non-secret model configuration using canonical JSON."""
    tool_names = tuple(
        name for tool in request.tools if isinstance((name := tool.get("name")), str)
    )
    value = {
        "settings": asdict(settings),
        "request": {"max_tokens": request.max_tokens, "tool_names": tool_names},
    }
    return canonical_hash(value)


def tool_schema_hash(schemas: Sequence[Mapping[str, object]]) -> str:
    """Hash the Anthropic wire structure of every model-visible tool schema."""
    wire = [
        {
            "name": schema.get("name"),
            "description": schema.get("description"),
            "input_schema": schema.get("input_schema"),
        }
        for schema in schemas
    ]
    return canonical_hash(wire)


def args_hash(canonical_arguments: str) -> str:
    """Hash already-canonical tool arguments so exports never carry the values."""
    return hashlib.sha256(canonical_arguments.encode("utf-8")).hexdigest()


def build_run_report(
    store: SQLiteStore,
    run: Run,
    *,
    tool_schemas: Sequence[Mapping[str, object]],
    agent_monotonic_ms: int,
) -> dict[str, object]:
    """Project one finished run into the versioned, redacted agent-side report."""
    requests = store.model_requests_for_report(run.id)
    tools = store.tool_executions_for_report(run.id)
    snapshot = store.load_context_snapshot(run.session_id)
    main = [item for item in requests if item["kind"] == "main"]
    compaction = [item for item in requests if item["kind"] == "compaction"]
    tool_facts = _tool_facts(tools)
    return {
        "schema_version": RUN_REPORT_SCHEMA_VERSION,
        "run_id": run.id,
        "session_id": run.session_id,
        "state": run.state.name,
        "stop_reason": run.stop_reason.name if run.stop_reason is not None else None,
        "error_kind": run.error_kind.name if run.error_kind is not None else None,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at is not None else None,
        "tool_schema_hash": tool_schema_hash(tool_schemas),
        "model_identity": _model_identity(run.config_snapshot),
        "model": {"main": _model_facts(main), "compaction": _model_facts(compaction)},
        "tools": tool_facts,
        "compaction": _compaction_facts(snapshot, requests),
        "durations": {
            "agent_monotonic_ms": agent_monotonic_ms,
            "retry_wait_monotonic_ms": sum(int(item["total_wait_ms"] or 0) for item in requests),
            "tool_execution_monotonic_ms": tool_facts["duration_ms"],
            "model_request_elapsed_ms": _elapsed_ms(main),
            "compaction_request_elapsed_ms": _elapsed_ms(compaction),
        },
    }


def _model_identity(config_snapshot: Mapping[str, object]) -> dict[str, object | None]:
    """Project the model identity out of the run's own persisted configuration snapshot.

    The snapshot is the configuration this run actually served its requests with, so it
    cannot drift from the process that produced the numbers. The credential and the API
    endpoint are deliberately left out: neither identifies a model, and a published report
    must not carry them.
    """
    values = config_snapshot.get("model")
    model = values if isinstance(values, Mapping) else {}
    name = model.get("model")
    stream = model.get("stream")
    return {
        "name": name if isinstance(name, str) else None,
        "context_window": _setting_int(model.get("context_window")),
        "max_output_tokens": _setting_int(model.get("max_output_tokens")),
        "stream": stream if isinstance(stream, bool) else None,
    }


def _setting_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _model_facts(requests: Sequence[Mapping[str, object]]) -> dict[str, object]:
    finished = [item for item in requests if item["finished_at"] is not None]
    with_usage = [item for item in finished if item["usage_source"] == "provider"]
    return {
        "requests": len(requests),
        "attempts": sum(int(item["attempt_count"] or 0) for item in requests),
        "network_retries": sum(int(item["network_retry_count"] or 0) for item in requests),
        "results": _result_counts(requests),
        "usage": {name: _usage_total(finished, name) for name in _USAGE_COMPONENTS},
        "usage_coverage": (len(with_usage) / len(finished)) if finished else None,
        "elapsed_ms": _elapsed_ms(requests),
    }


def _result_counts(requests: Sequence[Mapping[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in requests:
        name = item["result"] if isinstance(item["result"], str) else "unfinished"
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def _usage_total(requests: Sequence[Mapping[str, object]], component: str) -> int | None:
    """Keep a component ``None`` whenever any request lacks provider usage for it."""
    if not requests:
        return None
    values = [item[component] for item in requests]
    if any(value is None for value in values):
        return None
    return sum(int(value) for value in values if value is not None)


def _tool_facts(executions: Sequence[Mapping[str, object]]) -> dict[str, object]:
    calls: list[dict[str, object]] = []
    signatures: set[tuple[str, str]] = set()
    duplicates = 0
    for item in executions:
        fingerprint = (str(item["name"]), args_hash(str(item["args_json"])))
        if fingerprint in signatures:
            duplicates += 1
        signatures.add(fingerprint)
        calls.append(
            {
                "name": item["name"],
                "args_hash": fingerprint[1],
                "state": item["execution_state"],
                "executed": bool(item["effect_started"]),
                "duration_ms": item["duration_ms"],
                "output_bytes": item["output_bytes"],
                "truncated": bool(item["truncated"]),
            }
        )
    by_name: dict[str, dict[str, int]] = {}
    for call in calls:
        entry = by_name.setdefault(str(call["name"]), {"proposed": 0, "succeeded": 0, "failed": 0})
        entry["proposed"] += 1
        if call["state"] == "succeeded":
            entry["succeeded"] += 1
        elif call["state"] == "failed":
            entry["failed"] += 1
    facts: dict[str, object] = {
        "proposed": len(calls),
        "executed": sum(1 for call in calls if call["executed"]),
        "duplicate_calls": duplicates,
        "output_bytes": sum(int(call["output_bytes"] or 0) for call in calls),
        "duration_ms": sum(int(call["duration_ms"] or 0) for call in calls),
        "truncated": sum(1 for call in calls if call["truncated"]),
        "by_name": dict(sorted(by_name.items())),
        "calls": calls,
    }
    for state in _TOOL_STATES:
        facts[state] = sum(1 for call in calls if call["state"] == state)
    return facts


def _compaction_facts(
    snapshot: object,
    requests: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    compaction = [item for item in requests if item["kind"] == "compaction"]
    output_tokens = next(
        (
            int(item["output_tokens"])
            for item in reversed(compaction)
            if item["output_tokens"] is not None
        ),
        None,
    )
    estimate = getattr(snapshot, "token_estimate", None) if snapshot is not None else None
    delta = None if estimate is None or output_tokens is None else estimate - output_tokens
    return {
        "count": getattr(snapshot, "version", 0) if snapshot is not None else 0,
        "requests": len(compaction),
        "above_target": bool(getattr(snapshot, "compaction_above_target", False)),
        "estimator_id": getattr(snapshot, "estimator_id", None) if snapshot is not None else None,
        "input_tokens_before": _boundary_tokens(requests, before=True),
        "input_tokens_after": _boundary_tokens(requests, before=False),
        "estimate_error": {
            "estimated_summary_tokens": estimate,
            "provider_summary_output_tokens": output_tokens,
            "estimated_minus_provider_tokens": delta,
        },
    }


def _boundary_tokens(requests: Sequence[Mapping[str, object]], *, before: bool) -> int | None:
    """Return provider input tokens for the main request framing the compaction window."""
    positions = [index for index, item in enumerate(requests) if item["kind"] == "compaction"]
    if not positions:
        return None
    if before:
        candidates = [
            item
            for item in requests[: positions[0]]
            if item["kind"] == "main" and item["input_tokens"] is not None
        ]
        return int(candidates[-1]["input_tokens"]) if candidates else None
    candidates = [
        item
        for item in requests[positions[-1] + 1 :]
        if item["kind"] == "main" and item["input_tokens"] is not None
    ]
    return int(candidates[0]["input_tokens"]) if candidates else None


def _elapsed_ms(requests: Sequence[Mapping[str, object]]) -> int:
    total = 0.0
    for item in requests:
        started = item["started_at"]
        finished = item["finished_at"]
        if not isinstance(started, str) or not isinstance(finished, str):
            continue
        total += (
            datetime.fromisoformat(finished) - datetime.fromisoformat(started)
        ).total_seconds()
    return max(0, round(total * 1000))


__all__ = [
    "RUN_REPORT_SCHEMA_VERSION",
    "args_hash",
    "build_run_report",
    "canonical_hash",
    "canonical_json",
    "model_config_hash",
    "tool_schema_hash",
]
