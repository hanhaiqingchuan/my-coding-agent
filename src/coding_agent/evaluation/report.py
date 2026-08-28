"""Scoring and versioned serialization for evaluation runs.

Nothing in this module reads the agent's database. Every agent fact arrives here
already projected into the agent's own ``run-report-v1`` document; this module only
adds harness facts, applies the fixed success definition, and serializes results.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

RUN_SCHEMA_VERSION = "run-v1"
SUMMARY_SCHEMA_VERSION = "summary-v1"
PROVIDER = "anthropic_messages"
AGENT_ARGV_OPTIONS = (
    "--config",
    "--workspace",
    "--data-dir",
    "--prompt-file",
    "--report-out",
    "--yes",
    "--ack-unsafe-auto-approve",
    "--command-policy",
)
OUTCOME_OK = "OK"
OUTCOME_HARNESS_SETUP = "HARNESS_SETUP"
OUTCOME_HARNESS_ORACLE_ERROR = "HARNESS_ORACLE_ERROR"
HARNESS_OUTCOMES = (OUTCOME_HARNESS_SETUP, OUTCOME_HARNESS_ORACLE_ERROR)
_TOOL_COUNTER_NAMES = (
    "proposed",
    "executed",
    "succeeded",
    "failed",
    "skipped",
    "duplicate_calls",
    "truncated",
    "output_bytes",
)


class ReportError(ValueError):
    """Raised when a result export is unreadable, unknown, or would be overwritten."""


@dataclass(slots=True)
class UsageFacts:
    """Provider usage components; a missing component always stays ``None``."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None

    def to_document(self) -> dict[str, int | None]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }


@dataclass(slots=True)
class ModelFacts:
    usage: UsageFacts = field(default_factory=UsageFacts)
    main_requests: int = 0
    compaction_requests: int = 0
    attempts: int = 0
    network_retries: int = 0
    usage_coverage: float | None = None

    def to_document(self) -> dict[str, object]:
        return {
            "usage": self.usage.to_document(),
            "main_requests": self.main_requests,
            "compaction_requests": self.compaction_requests,
            "attempts": self.attempts,
            "network_retries": self.network_retries,
            "usage_coverage": self.usage_coverage,
        }


@dataclass(slots=True)
class ToolFacts:
    proposed: int = 0
    executed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    duplicate_calls: int = 0
    output_bytes: int = 0
    truncated: int = 0

    def to_document(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in _TOOL_COUNTER_NAMES}


@dataclass(slots=True)
class CompactionFacts:
    count: int = 0
    requests: int = 0
    above_target: bool = False
    input_tokens_before: int | None = None
    input_tokens_after: int | None = None
    estimated_summary_tokens: int | None = None
    provider_summary_output_tokens: int | None = None
    estimated_minus_provider_tokens: int | None = None

    def to_document(self) -> dict[str, object]:
        return {
            "count": self.count,
            "requests": self.requests,
            "above_target": self.above_target,
            "input_tokens_before": self.input_tokens_before,
            "input_tokens_after": self.input_tokens_after,
            "estimated_summary_tokens": self.estimated_summary_tokens,
            "provider_summary_output_tokens": self.provider_summary_output_tokens,
            "estimated_minus_provider_tokens": self.estimated_minus_provider_tokens,
        }


@dataclass(slots=True)
class OracleFacts:
    passed: bool | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    errored: bool = False

    def to_document(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "errored": self.errored,
        }


@dataclass(slots=True)
class ModificationFacts:
    files_added: int = 0
    files_modified: int = 0
    files_deleted: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    out_of_scope_paths: list[str] = field(default_factory=list)

    def to_document(self, *, forbidden: Sequence[str], escaped: bool) -> dict[str, object]:
        return {
            "files_added": self.files_added,
            "files_modified": self.files_modified,
            "files_deleted": self.files_deleted,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "out_of_scope_paths": list(self.out_of_scope_paths),
            "forbidden_paths_modified": list(forbidden),
            "detected_workspace_escape": escaped,
        }


@dataclass(slots=True)
class DurationFacts:
    """Harness durations always come from a monotonic clock."""

    workspace_prepare_ms: int = 0
    agent_process_ms: int = 0
    oracle_ms: int = 0
    total_ms: int = 0
    agent_monotonic_ms: int | None = None
    retry_wait_monotonic_ms: int | None = None
    tool_execution_monotonic_ms: int | None = None

    def to_document(self) -> dict[str, int | None]:
        return {
            "workspace_prepare_ms": self.workspace_prepare_ms,
            "agent_process_ms": self.agent_process_ms,
            "oracle_ms": self.oracle_ms,
            "total_ms": self.total_ms,
            "agent_monotonic_ms": self.agent_monotonic_ms,
            "retry_wait_monotonic_ms": self.retry_wait_monotonic_ms,
            "tool_execution_monotonic_ms": self.tool_execution_monotonic_ms,
        }


@dataclass(slots=True)
class RunResult:
    """One immutable evaluation run: harness observations plus the agent's own report."""

    task_id: str
    category: str
    repeat: int
    outcome: str = OUTCOME_OK
    state: str | None = None
    stop_reason: str | None = None
    error_kind: str | None = None
    oracle_passed: bool = False
    regressions_passed: bool = False
    forbidden_changes: list[str] = field(default_factory=list)
    detected_workspace_escape: bool = False
    strict_success: bool = False
    artifact_correct: bool = False
    failure_stage: str | None = None
    failure_kind: str | None = None
    model: ModelFacts = field(default_factory=ModelFacts)
    tools: ToolFacts = field(default_factory=ToolFacts)
    compaction: CompactionFacts = field(default_factory=CompactionFacts)
    target_oracle: OracleFacts = field(default_factory=OracleFacts)
    regression_oracle: OracleFacts = field(default_factory=OracleFacts)
    modifications: ModificationFacts = field(default_factory=ModificationFacts)
    durations: DurationFacts = field(default_factory=DurationFacts)
    hashes: dict[str, str | None] = field(default_factory=dict)
    agent_commit: str | None = None
    agent_report: Mapping[str, object] | None = None
    agent_exit_code: int | None = None
    agent_timed_out: bool = False
    harness_detail: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


def compute_strict_success(result: RunResult) -> bool:
    """Apply the fixed primary metric: correct artifact and a normally finished run."""
    return compute_artifact_correct(result) and result.state == "COMPLETED"


def compute_artifact_correct(result: RunResult) -> bool:
    """Report artifact quality without crediting the agent's run control."""
    return (
        result.outcome == OUTCOME_OK
        and result.oracle_passed
        and result.regressions_passed
        and not result.forbidden_changes
        and not result.detected_workspace_escape
    )


_STOP_REASON_FAILURES: dict[str, tuple[str, str]] = {
    "AUTH_ERROR": ("model", "model_auth"),
    "RETRY_EXHAUSTED": ("model", "model_transport"),
    "MODEL_PROTOCOL_ERROR": ("model", "model_protocol"),
    "INCOMPLETE_TOOL_CALL": ("model", "model_protocol"),
    "MODEL_REFUSAL": ("model", "model_refusal"),
    "PAUSE_TURN": ("model", "pause_turn"),
    "OUTPUT_TRUNCATED": ("model", "output_truncated"),
    "CONTEXT_OVERFLOW": ("agent", "context_overflow"),
    "MAX_ROUNDS": ("agent", "max_rounds"),
    "DOOM_LOOP": ("agent", "doom_loop"),
    "EMPTY_RESPONSE": ("agent", "doom_loop"),
    "CONFIG_ERROR": ("setup", "harness_setup"),
}


def classify_failure(result: RunResult) -> tuple[str | None, str | None]:
    """Return the fixed ``(failure_stage, failure_kind)`` pair for one run."""
    if compute_strict_success(result):
        return None, None
    if result.outcome == OUTCOME_HARNESS_SETUP:
        return "setup", "harness_setup"
    if result.outcome == OUTCOME_HARNESS_ORACLE_ERROR:
        return "oracle", "harness_oracle"
    if result.detected_workspace_escape or result.forbidden_changes:
        return "agent", "forbidden_modification"
    by_stop_reason = _STOP_REASON_FAILURES.get(result.stop_reason or "")
    if by_stop_reason is not None:
        return by_stop_reason
    if result.state == "COMPLETED":
        return "oracle", "oracle_failure"
    if result.tools.failed:
        return "tool", "tool_execution"
    return "agent", "unknown"


def run_document(result: RunResult, *, campaign_id: str) -> dict[str, object]:
    """Serialize one run as the versioned, redacted ``run-v1`` document."""
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "task_id": result.task_id,
        "category": result.category,
        "repeat": result.repeat,
        "provider": PROVIDER,
        "agent_commit": result.agent_commit,
        "outcome": result.outcome,
        "strict_success": result.strict_success,
        "artifact_correct": result.artifact_correct,
        "state": result.state,
        "stop_reason": result.stop_reason,
        "error_kind": result.error_kind,
        "failure_stage": result.failure_stage,
        "failure_kind": result.failure_kind,
        "harness_detail": result.harness_detail,
        "agent_exit_code": result.agent_exit_code,
        "agent_timed_out": result.agent_timed_out,
        "agent_argv_options": list(AGENT_ARGV_OPTIONS),
        "hashes": dict(result.hashes),
        "model": result.model.to_document(),
        "tools": result.tools.to_document(),
        "compaction": result.compaction.to_document(),
        "oracle": {
            "target": result.target_oracle.to_document(),
            "regression": result.regression_oracle.to_document(),
        },
        "modifications": result.modifications.to_document(
            forbidden=result.forbidden_changes,
            escaped=result.detected_workspace_escape,
        ),
        "durations": result.durations.to_document(),
        "agent_report": dict(result.agent_report or {}),
        "started_at": result.started_at,
        "finished_at": result.finished_at,
    }


@dataclass(frozen=True, slots=True)
class TaskSummary:
    task_id: str
    category: str
    started_runs: int
    valid_runs: int
    strict_success_runs: int
    artifact_correct_runs: int
    results: tuple[bool, ...]
    robust: bool

    def to_document(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "category": self.category,
            "started_runs": self.started_runs,
            "valid_runs": self.valid_runs,
            "strict_success_runs": self.strict_success_runs,
            "artifact_correct_runs": self.artifact_correct_runs,
            "results": list(self.results),
            "robust": self.robust,
        }


@dataclass(frozen=True, slots=True)
class Summary:
    """A redacted campaign aggregate; token totals stay ``None`` when usage is missing."""

    campaign_id: str | None
    agent_commit: str | None
    started_runs: int
    valid_runs: int
    harness_setup_runs: int
    harness_oracle_error_runs: int
    strict_success_runs: int
    artifact_correct_runs: int
    artifact_correct_only_runs: int
    task_completion_rate: float | None
    robust_task_count: int
    total_input_tokens: int | None
    total_output_tokens: int | None
    total_cache_creation_input_tokens: int | None
    total_cache_read_input_tokens: int | None
    usage_coverage: float | None
    total_main_requests: int
    total_compaction_requests: int
    total_attempts: int
    total_network_retries: int
    tools: Mapping[str, int]
    compaction_runs: int
    agent_monotonic_ms: Mapping[str, float | None]
    failure_kinds: Mapping[str, int]
    tasks: tuple[TaskSummary, ...]

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "campaign_id": self.campaign_id,
            "agent_commit": self.agent_commit,
            "provider": PROVIDER,
            "started_runs": self.started_runs,
            "valid_runs": self.valid_runs,
            "harness_setup_runs": self.harness_setup_runs,
            "harness_oracle_error_runs": self.harness_oracle_error_runs,
            "strict_success_runs": self.strict_success_runs,
            "artifact_correct_runs": self.artifact_correct_runs,
            "artifact_correct_only_runs": self.artifact_correct_only_runs,
            "task_completion_rate": self.task_completion_rate,
            "robust_task_count": self.robust_task_count,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_creation_input_tokens": self.total_cache_creation_input_tokens,
            "total_cache_read_input_tokens": self.total_cache_read_input_tokens,
            "usage_coverage": self.usage_coverage,
            "total_main_requests": self.total_main_requests,
            "total_compaction_requests": self.total_compaction_requests,
            "total_attempts": self.total_attempts,
            "total_network_retries": self.total_network_retries,
            "tools": dict(self.tools),
            "compaction_runs": self.compaction_runs,
            "agent_monotonic_ms": dict(self.agent_monotonic_ms),
            "failure_kinds": dict(self.failure_kinds),
            "tasks": [task.to_document() for task in self.tasks],
        }


def summarize(
    results: Sequence[RunResult],
    *,
    campaign_id: str | None = None,
    agent_commit: str | None = None,
) -> Summary:
    """Aggregate runs while keeping harness outcomes out of the capability denominator."""
    valid = [item for item in results if item.outcome == OUTCOME_OK]
    strict = [item for item in valid if item.strict_success or compute_strict_success(item)]
    artifact = [item for item in valid if item.artifact_correct or compute_artifact_correct(item)]
    durations = [
        float(item.durations.agent_monotonic_ms)
        for item in valid
        if item.durations.agent_monotonic_ms is not None
    ]
    tasks = _task_summaries(results)
    return Summary(
        campaign_id=campaign_id,
        agent_commit=agent_commit or next((item.agent_commit for item in results), None),
        started_runs=len(results),
        valid_runs=len(valid),
        harness_setup_runs=sum(1 for item in results if item.outcome == OUTCOME_HARNESS_SETUP),
        harness_oracle_error_runs=sum(
            1 for item in results if item.outcome == OUTCOME_HARNESS_ORACLE_ERROR
        ),
        strict_success_runs=len(strict),
        artifact_correct_runs=len(artifact),
        artifact_correct_only_runs=len(artifact) - len(strict),
        task_completion_rate=(len(strict) / len(valid)) if valid else None,
        robust_task_count=sum(1 for task in tasks if task.robust),
        total_input_tokens=_total(valid, "input_tokens"),
        total_output_tokens=_total(valid, "output_tokens"),
        total_cache_creation_input_tokens=_total(valid, "cache_creation_input_tokens"),
        total_cache_read_input_tokens=_total(valid, "cache_read_input_tokens"),
        usage_coverage=_mean([item.model.usage_coverage for item in valid]),
        total_main_requests=sum(item.model.main_requests for item in valid),
        total_compaction_requests=sum(item.model.compaction_requests for item in valid),
        total_attempts=sum(item.model.attempts for item in valid),
        total_network_retries=sum(item.model.network_retries for item in valid),
        tools={
            name: sum(getattr(item.tools, name) for item in valid) for name in _TOOL_COUNTER_NAMES
        },
        compaction_runs=sum(1 for item in valid if item.compaction.count > 0),
        agent_monotonic_ms=_distribution(durations),
        failure_kinds=_failure_kinds(results),
        tasks=tasks,
    )


def summarize_campaign(input_dir: Path, output_dir: Path) -> Summary:
    """Read one campaign's immutable run records and write its redacted aggregates."""
    documents = _read_documents(input_dir)
    results = [_result_from_document(document) for document in documents]
    campaign_id = next((document.get("campaign_id") for document in documents), None)
    commit = next((document.get("agent_commit") for document in documents), None)
    summary = summarize(
        results,
        campaign_id=campaign_id if isinstance(campaign_id, str) else None,
        agent_commit=commit if isinstance(commit, str) else None,
    )
    _write_outputs(output_dir, summary, results)
    return summary


def _read_documents(input_dir: Path) -> list[Mapping[str, object]]:
    lines_file = input_dir / "runs.jsonl"
    if not lines_file.is_file():
        raise ReportError(f"input: no runs.jsonl found in {input_dir}")
    documents: list[Mapping[str, object]] = []
    for number, line in enumerate(lines_file.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            document = json.loads(line)
        except json.JSONDecodeError as error:
            raise ReportError(f"runs.jsonl line {number}: invalid JSON") from error
        if not isinstance(document, dict) or document.get("schema_version") != RUN_SCHEMA_VERSION:
            raise ReportError(f"runs.jsonl line {number}: expected schema_version run-v1")
        documents.append(document)
    if not documents:
        raise ReportError("runs.jsonl: contains no run records")
    return documents


def _result_from_document(document: Mapping[str, object]) -> RunResult:
    model = _mapping(document.get("model"))
    usage = _mapping(model.get("usage"))
    tools = _mapping(document.get("tools"))
    compaction = _mapping(document.get("compaction"))
    modifications = _mapping(document.get("modifications"))
    durations = _mapping(document.get("durations"))
    result = RunResult(
        task_id=str(document.get("task_id")),
        category=str(document.get("category")),
        repeat=int(document.get("repeat") or 0),
        outcome=str(document.get("outcome") or OUTCOME_OK),
        state=_optional_str(document.get("state")),
        stop_reason=_optional_str(document.get("stop_reason")),
        error_kind=_optional_str(document.get("error_kind")),
        strict_success=bool(document.get("strict_success")),
        artifact_correct=bool(document.get("artifact_correct")),
        failure_stage=_optional_str(document.get("failure_stage")),
        failure_kind=_optional_str(document.get("failure_kind")),
        agent_commit=_optional_str(document.get("agent_commit")),
    )
    result.oracle_passed = bool(result.strict_success or result.artifact_correct)
    result.regressions_passed = result.oracle_passed
    result.forbidden_changes = [
        str(item) for item in _sequence(modifications.get("forbidden_paths_modified"))
    ]
    result.detected_workspace_escape = bool(modifications.get("detected_workspace_escape"))
    result.model = ModelFacts(
        usage=UsageFacts(
            input_tokens=_optional_int(usage.get("input_tokens")),
            output_tokens=_optional_int(usage.get("output_tokens")),
            cache_creation_input_tokens=_optional_int(usage.get("cache_creation_input_tokens")),
            cache_read_input_tokens=_optional_int(usage.get("cache_read_input_tokens")),
        ),
        main_requests=int(model.get("main_requests") or 0),
        compaction_requests=int(model.get("compaction_requests") or 0),
        attempts=int(model.get("attempts") or 0),
        network_retries=int(model.get("network_retries") or 0),
        usage_coverage=_optional_float(model.get("usage_coverage")),
    )
    result.tools = ToolFacts(**{name: int(tools.get(name) or 0) for name in _TOOL_COUNTER_NAMES})
    result.compaction = CompactionFacts(
        count=int(compaction.get("count") or 0),
        requests=int(compaction.get("requests") or 0),
        above_target=bool(compaction.get("above_target")),
    )
    result.durations = DurationFacts(
        workspace_prepare_ms=int(durations.get("workspace_prepare_ms") or 0),
        agent_process_ms=int(durations.get("agent_process_ms") or 0),
        oracle_ms=int(durations.get("oracle_ms") or 0),
        total_ms=int(durations.get("total_ms") or 0),
        agent_monotonic_ms=_optional_int(durations.get("agent_monotonic_ms")),
    )
    return result


def _write_outputs(
    output_dir: Path,
    summary: Summary,
    results: Sequence[RunResult],
) -> None:
    targets = {name: output_dir / name for name in ("summary.json", "summary.csv", "report.md")}
    existing = sorted(name for name, path in targets.items() if path.exists())
    if existing:
        raise ReportError(f"output: {existing[0]} already exists and must not be overwritten")
    output_dir.mkdir(parents=True, exist_ok=True)
    targets["summary.json"].write_text(
        json.dumps(summary.to_document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with targets["summary.csv"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_CSV_FIELDS))
        writer.writeheader()
        for item in results:
            writer.writerow(
                {
                    "task_id": item.task_id,
                    "category": item.category,
                    "repeat": item.repeat,
                    "outcome": item.outcome,
                    "strict_success": item.strict_success,
                    "artifact_correct": item.artifact_correct,
                    "state": item.state,
                    "stop_reason": item.stop_reason,
                    "failure_stage": item.failure_stage,
                    "failure_kind": item.failure_kind,
                    "input_tokens": item.model.usage.input_tokens,
                    "output_tokens": item.model.usage.output_tokens,
                    "cache_creation_input_tokens": (item.model.usage.cache_creation_input_tokens),
                    "cache_read_input_tokens": item.model.usage.cache_read_input_tokens,
                    "main_requests": item.model.main_requests,
                    "compaction_requests": item.model.compaction_requests,
                    "attempts": item.model.attempts,
                    "agent_monotonic_ms": item.durations.agent_monotonic_ms,
                    "oracle_ms": item.durations.oracle_ms,
                }
            )
    targets["report.md"].write_text(_markdown(summary), encoding="utf-8")


_CSV_FIELDS = (
    "task_id",
    "category",
    "repeat",
    "outcome",
    "strict_success",
    "artifact_correct",
    "state",
    "stop_reason",
    "failure_stage",
    "failure_kind",
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "main_requests",
    "compaction_requests",
    "attempts",
    "agent_monotonic_ms",
    "oracle_ms",
)


def _markdown(summary: Summary) -> str:
    lines = [
        f"# Evaluation summary {summary.campaign_id or '(unnamed campaign)'}",
        "",
        f"- provider: `{PROVIDER}`",
        f"- agent commit: `{summary.agent_commit or 'unknown'}`",
        f"- started runs: {summary.started_runs}",
        f"- valid runs: {summary.valid_runs}",
        f"- harness setup outcomes: {summary.harness_setup_runs}",
        f"- harness oracle errors: {summary.harness_oracle_error_runs}",
        f"- strict successes: {summary.strict_success_runs}",
        f"- artifact correct only: {summary.artifact_correct_only_runs}",
        f"- task completion rate: {_percent(summary.task_completion_rate)}",
        f"- robust tasks: {summary.robust_task_count}",
        f"- total input tokens: {_number(summary.total_input_tokens)}",
        f"- total output tokens: {_number(summary.total_output_tokens)}",
        "",
        "| task | category | valid | strict | results |",
        "| --- | --- | --- | --- | --- |",
    ]
    for task in summary.tasks:
        results = " ".join("pass" if value else "fail" for value in task.results)
        lines.append(
            f"| {task.task_id} | {task.category} | {task.valid_runs} "
            f"| {task.strict_success_runs} | {results} |"
        )
    lines.append("")
    return "\n".join(lines)


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _number(value: int | None) -> str:
    return "null" if value is None else str(value)


def _total(results: Sequence[RunResult], component: str) -> int | None:
    values = [getattr(item.model.usage, component) for item in results]
    if not values or any(value is None for value in values):
        return None
    return sum(int(value) for value in values if value is not None)


def _mean(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _distribution(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"total": None, "mean": None, "median": None, "min": None, "max": None, "p95": None}
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return {
        "total": sum(ordered),
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "p95": ordered[index],
    }


def _failure_kinds(results: Iterable[RunResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        stage, kind = (item.failure_stage, item.failure_kind)
        if kind is None:
            stage, kind = classify_failure(item)
        if kind is None:
            continue
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def _task_summaries(results: Sequence[RunResult]) -> tuple[TaskSummary, ...]:
    ordered: dict[str, list[RunResult]] = {}
    for item in results:
        ordered.setdefault(item.task_id, []).append(item)
    summaries: list[TaskSummary] = []
    for task_id, items in ordered.items():
        valid = [entry for entry in items if entry.outcome == OUTCOME_OK]
        flags = tuple(
            bool(entry.strict_success or compute_strict_success(entry))
            for entry in sorted(valid, key=lambda entry: entry.repeat)
        )
        successes = sum(1 for value in flags if value)
        summaries.append(
            TaskSummary(
                task_id=task_id,
                category=items[0].category,
                started_runs=len(items),
                valid_runs=len(valid),
                strict_success_runs=successes,
                artifact_correct_runs=sum(
                    1
                    for entry in valid
                    if entry.artifact_correct or compute_artifact_correct(entry)
                ),
                results=flags,
                robust=bool(flags) and successes * 2 >= len(flags) and successes >= 2,
            )
        )
    return tuple(summaries)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, list) else ()


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


ResultWriter = Callable[[Path, RunResult, str], None]


def write_run_document(path: Path, result: RunResult, campaign_id: str) -> None:
    """Persist one immutable ``run-v1`` document, never overwriting an existing record."""
    if path.exists():
        raise ReportError(f"run: {path.name} already exists and must not be overwritten")
    document = run_document(result, campaign_id=campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "AGENT_ARGV_OPTIONS",
    "CompactionFacts",
    "DurationFacts",
    "HARNESS_OUTCOMES",
    "ModelFacts",
    "ModificationFacts",
    "OUTCOME_HARNESS_ORACLE_ERROR",
    "OUTCOME_HARNESS_SETUP",
    "OUTCOME_OK",
    "OracleFacts",
    "PROVIDER",
    "RUN_SCHEMA_VERSION",
    "ReportError",
    "RunResult",
    "SUMMARY_SCHEMA_VERSION",
    "Summary",
    "TaskSummary",
    "ToolFacts",
    "UsageFacts",
    "classify_failure",
    "compute_artifact_correct",
    "compute_strict_success",
    "run_document",
    "summarize",
    "summarize_campaign",
    "write_run_document",
]
