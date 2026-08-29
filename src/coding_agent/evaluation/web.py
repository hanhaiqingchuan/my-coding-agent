"""The read-only evaluation results API.

Three GET endpoints mirror the campaign history index and the immutable records a
campaign writes: the campaign list, one campaign's task rows with aggregates, and
one run document beside its judgement. Nothing here writes, moves or deletes any
file under the results root, and every reader degrades a corrupt record to a null
field with a note instead of failing the request.

The responses carry only redacted documents: run-v1 and judgement-v1 records are
path-free and credential-free by construction, and the campaign identity is the
directory name, never an absolute path.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError

from coding_agent.api.dependencies import ApiDependencies, get_api_dependencies
from coding_agent.api.schemas import StrictDto
from coding_agent.evaluation.history import (
    CampaignSummary,
    HistoryError,
    read_judgements,
    read_run_documents,
    scan_campaigns,
)
from coding_agent.evaluation.judge import JUDGE_ERROR, JUDGEMENT_SCHEMA_VERSION, SCORE_NAMES
from coding_agent.evaluation.report import (
    RUN_SCHEMA_VERSION,
    SUMMARY_SCHEMA_VERSION,
    result_from_document,
    summarize,
)


def require_browser_origin(request: Request) -> None:
    """Read endpoints need no CSRF token, but a present Origin must still be ours."""
    dependencies = get_api_dependencies(request)
    origin = request.headers.get("origin")
    if origin is not None and not dependencies.origin_allowed(
        origin,
        host=request.headers.get("host"),
        scheme=request.url.scheme,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ORIGIN_FORBIDDEN", "message": "request origin is not allowed"},
        )


router = APIRouter(prefix="/api/evaluations", dependencies=[Depends(require_browser_origin)])


# --- DTOs -------------------------------------------------------------------


class CampaignSummaryDto(StrictDto):
    """The headline numbers of one campaign, exactly as the history index scans them."""

    campaign_id: str | None
    directory: str
    started_at: str | None
    finished_at: str | None
    task_count: int
    started_runs: int
    valid_runs: int
    strict_success_runs: int
    strict_success_rate: float | None
    judged_runs: int
    judge_error_runs: int
    judge_means: dict[str, float | None]
    model_name: str | None
    judge_model: str | None
    corrupt: bool
    note: str | None

    @classmethod
    def from_domain(cls, summary: CampaignSummary) -> CampaignSummaryDto:
        return cls(
            campaign_id=summary.campaign_id,
            directory=summary.directory,
            started_at=summary.started_at,
            finished_at=summary.finished_at,
            task_count=summary.task_count,
            started_runs=summary.started_runs,
            valid_runs=summary.valid_runs,
            strict_success_runs=summary.strict_success_runs,
            strict_success_rate=summary.strict_success_rate,
            judged_runs=summary.judged_runs,
            judge_error_runs=summary.judge_error_runs,
            judge_means={name: summary.judge_means.get(name) for name in SCORE_NAMES},
            model_name=summary.model_name,
            judge_model=summary.judge_model,
            corrupt=summary.corrupt,
            note=summary.note,
        )


class JudgeScoresDto(StrictDto):
    """The three 1-5 judge scores; a component the record does not carry stays null."""

    task_completion: int | None = None
    process_quality: int | None = None
    communication: int | None = None


class RunRowDto(StrictDto):
    """One run's deterministic metrics: rounds, tool calls, tokens, durations, outcome."""

    task_id: str
    repeat: int
    category: str
    outcome: str
    strict_success: bool
    artifact_correct: bool
    state: str | None
    stop_reason: str | None
    failure_stage: str | None
    failure_kind: str | None
    rounds: int | None
    tool_calls: int | None
    input_tokens: int | None
    output_tokens: int | None
    agent_ms: int | None
    total_ms: int | None
    judge_scores: JudgeScoresDto | None
    judge_error: bool

    @classmethod
    def from_documents(
        cls, document: Mapping[str, object], judgement: Mapping[str, object] | None
    ) -> RunRowDto:
        model = _mapping(document.get("model"))
        usage = _mapping(model.get("usage"))
        tools = _mapping(document.get("tools"))
        durations = _mapping(document.get("durations"))
        scores = JudgeScoresDto()
        judge_error = False
        if judgement is not None:
            judge_error = judgement.get("error") == JUDGE_ERROR
            record = _mapping(judgement.get("scores"))
            scores = JudgeScoresDto(
                task_completion=_count(record.get("task_completion")),
                process_quality=_count(record.get("process_quality")),
                communication=_count(record.get("communication")),
            )
        return cls(
            task_id=_text(document.get("task_id")) or "",
            repeat=_count(document.get("repeat")) or 0,
            category=_text(document.get("category")) or "",
            outcome=_text(document.get("outcome")) or "",
            strict_success=bool(document.get("strict_success")),
            artifact_correct=bool(document.get("artifact_correct")),
            state=_text(document.get("state")),
            stop_reason=_text(document.get("stop_reason")),
            failure_stage=_text(document.get("failure_stage")),
            failure_kind=_text(document.get("failure_kind")),
            rounds=_count(model.get("main_requests")),
            tool_calls=_count(tools.get("executed")),
            input_tokens=_count(usage.get("input_tokens")),
            output_tokens=_count(usage.get("output_tokens")),
            agent_ms=_count(durations.get("agent_monotonic_ms")),
            total_ms=_count(durations.get("total_ms")),
            judge_scores=None if judge_error else (scores if judgement is not None else None),
            judge_error=judge_error,
        )


class TaskRunsDto(StrictDto):
    """Every run of one task, repeats ascending."""

    task_id: str
    category: str
    runs: list[RunRowDto]


class CampaignAggregatesDto(StrictDto):
    """The campaign aggregate the dashboard shows beside its task rows."""

    started_runs: int
    valid_runs: int
    strict_success_runs: int
    artifact_correct_runs: int
    task_completion_rate: float | None
    robust_task_count: int
    total_input_tokens: int | None
    total_output_tokens: int | None
    total_main_requests: int
    total_tool_calls: int
    judged_runs: int
    judge_error_runs: int
    judge_means: dict[str, float | None]
    failure_kinds: dict[str, int]


class CampaignDetailDto(StrictDto):
    summary: CampaignSummaryDto
    aggregates: CampaignAggregatesDto | None
    tasks: list[TaskRunsDto]
    note: str | None


class JudgementDto(StrictDto):
    """The verbatim judgement-v1 record of one run."""

    schema_version: Literal["judgement-v1"]
    campaign_id: str | None
    task_id: str
    repeat: int
    judge_model: str
    prompt_version: str
    scores: dict[str, int]
    rationale: str
    error: str | None
    error_detail: str | None


class RunV1UsageDto(StrictDto):
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None


class RunV1ModelDto(StrictDto):
    usage: RunV1UsageDto
    main_requests: int
    compaction_requests: int
    attempts: int
    network_retries: int | None = None
    usage_coverage: float | None = None


class RunV1ToolsDto(StrictDto):
    proposed: int
    executed: int
    succeeded: int
    failed: int
    skipped: int
    duplicate_calls: int | None = None
    truncated: int | None = None
    output_bytes: int | None = None


class RunV1CompactionDto(StrictDto):
    count: int
    requests: int
    above_target: bool
    input_tokens_before: int | None = None
    input_tokens_after: int | None = None
    estimated_summary_tokens: int | None = None
    provider_summary_output_tokens: int | None = None
    estimated_minus_provider_tokens: int | None = None


class RunV1OracleOutcomeDto(StrictDto):
    passed: bool | None
    exit_code: int | None
    duration_ms: int | None
    errored: bool


class RunV1OracleDto(StrictDto):
    target: RunV1OracleOutcomeDto
    regression: RunV1OracleOutcomeDto


class RunV1ModificationsDto(StrictDto):
    files_added: int
    files_modified: int
    files_deleted: int
    lines_added: int
    lines_removed: int
    out_of_scope_paths: list[str] = []
    forbidden_paths_modified: list[str]
    detected_workspace_escape: bool


class RunV1DurationsDto(StrictDto):
    workspace_prepare_ms: int
    agent_process_ms: int
    oracle_ms: int
    total_ms: int
    agent_monotonic_ms: int | None = None
    retry_wait_monotonic_ms: int | None = None
    tool_execution_monotonic_ms: int | None = None


class RunV1HashesDto(StrictDto):
    config: str | None
    task: str | None
    prompt: str | None
    tool_schema: str | None
    baseline_tree: str | None
    workspace_tree: str | None
    diff: str | None


class RunV1IdentityDto(StrictDto):
    name: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    stream: bool | None = None


class RunV1Dto(StrictDto):
    """The run-v1 document verbatim; only the embedded agent report stays free-form."""

    schema_version: Literal["run-v1"]
    campaign_id: str
    task_id: str
    category: str
    repeat: int
    provider: Literal["anthropic_messages"]
    agent_commit: str | None
    model_identity: RunV1IdentityDto | None
    outcome: str
    strict_success: bool
    artifact_correct: bool
    state: str | None
    stop_reason: str | None
    error_kind: str | None
    failure_stage: str | None
    failure_kind: str | None
    harness_detail: str | None = None
    agent_exit_code: int | None = None
    agent_timed_out: bool = False
    agent_argv_options: list[str] = []
    hashes: RunV1HashesDto
    model: RunV1ModelDto
    tools: RunV1ToolsDto
    compaction: RunV1CompactionDto
    oracle: RunV1OracleDto
    modifications: RunV1ModificationsDto
    durations: RunV1DurationsDto
    agent_report: dict[str, Any]
    started_at: str | None = None
    finished_at: str | None = None


class RunDetailDto(StrictDto):
    """One run's facts plus its judgement; null fields mark unreadable records."""

    campaign: str
    task_id: str
    repeat: int
    run: RunV1Dto | None
    run_note: str | None
    judgement: JudgementDto | None
    judgement_note: str | None


# --- endpoints ---------------------------------------------------------------


@router.get("", response_model=list[CampaignSummaryDto])
def list_campaigns(
    response: Response,
    dependencies: ApiDependencies = Depends(get_api_dependencies),
) -> list[CampaignSummaryDto]:
    """Index every campaign under the results root, newest first."""
    response.headers["Cache-Control"] = "no-store"
    summaries = sorted(
        _summaries(dependencies),
        key=lambda entry: (entry.started_at or "", entry.directory),
        reverse=True,
    )
    return [CampaignSummaryDto.from_domain(summary) for summary in summaries]


@router.get("/{campaign}", response_model=CampaignDetailDto)
def campaign_detail(
    campaign: str,
    response: Response,
    dependencies: ApiDependencies = Depends(get_api_dependencies),
) -> CampaignDetailDto:
    """Serve one campaign's task rows, judge scores and aggregates, read-only."""
    response.headers["Cache-Control"] = "no-store"
    summary, directory = _campaign(dependencies, campaign)
    documents, _ = read_run_documents(directory)
    judgements, _ = read_judgements(directory)
    tasks = _task_rows(documents, judgements)
    return CampaignDetailDto(
        summary=CampaignSummaryDto.from_domain(summary),
        aggregates=_aggregates(directory, documents, judgements),
        tasks=tasks,
        note=summary.note,
    )


@router.get("/{campaign}/runs/{task_id}/{repeat}", response_model=RunDetailDto)
def run_detail(
    campaign: str,
    task_id: str,
    repeat: int,
    response: Response,
    dependencies: ApiDependencies = Depends(get_api_dependencies),
) -> RunDetailDto:
    """Serve one run-v1 document beside its judgement record, when present."""
    response.headers["Cache-Control"] = "no-store"
    _, directory = _campaign(dependencies, campaign)
    if repeat < 1 or not _safe_segment(task_id):
        raise _not_found("RUN_NOT_FOUND", f"no run record for task {task_id} repeat {repeat}")
    documents, _ = read_run_documents(directory)
    document = _find_run(documents, task_id, repeat)
    run: RunV1Dto | None = None
    run_note: str | None = None
    if document is not None:
        run, run_note = _run_dto(document)
    else:
        single, single_note = _read_single_run(directory, task_id, repeat)
        if single is not None:
            run, run_note = _run_dto(single)
        elif single_note is not None:
            # The record exists on disk but cannot be read: degrade, never 404.
            run_note = single_note
        else:
            raise _not_found("RUN_NOT_FOUND", f"no run record for task {task_id} repeat {repeat}")
    judgement, judgement_note = _read_single_judgement(directory, task_id, repeat)
    return RunDetailDto(
        campaign=campaign,
        task_id=task_id,
        repeat=repeat,
        run=run,
        run_note=run_note,
        judgement=judgement,
        judgement_note=judgement_note,
    )


# --- helpers -----------------------------------------------------------------


def _summaries(dependencies: ApiDependencies) -> list[CampaignSummary]:
    """Scan the results root; an unconfigured or missing root is an empty history."""
    root = dependencies.evaluation_results_root
    if root is None or not root.is_dir():
        return []
    try:
        return scan_campaigns(root)
    except HistoryError:
        return []


def _campaign(dependencies: ApiDependencies, campaign: str) -> tuple[CampaignSummary, Path]:
    """Resolve a campaign by its directory name among the scanned entries only."""
    root = dependencies.evaluation_results_root
    for summary in _summaries(dependencies):
        if summary.directory == campaign:
            assert root is not None
            return summary, root / campaign
    raise _not_found("CAMPAIGN_NOT_FOUND", f"no evaluation campaign named {campaign}")


def _task_rows(
    documents: Sequence[Mapping[str, object]],
    judgements: Sequence[Mapping[str, object]],
) -> list[TaskRunsDto]:
    """Group run documents into one row per run, repeats ascending under its task."""
    by_task: dict[str, list[Mapping[str, object]]] = {}
    for document in documents:
        task_id = _text(document.get("task_id"))
        if task_id is None:
            continue
        by_task.setdefault(task_id, []).append(document)
    by_run = {(_text(item.get("task_id")), _count(item.get("repeat"))): item for item in judgements}
    tasks: list[TaskRunsDto] = []
    for task_id in sorted(by_task):
        runs = sorted(by_task[task_id], key=lambda item: _count(item.get("repeat")) or 0)
        categories = [_text(item.get("category")) for item in runs]
        tasks.append(
            TaskRunsDto(
                task_id=task_id,
                category=next((name for name in categories if name is not None), ""),
                runs=[
                    RunRowDto.from_documents(
                        item, by_run.get((task_id, _count(item.get("repeat"))))
                    )
                    for item in runs
                ],
            )
        )
    return tasks


def _aggregates(
    directory: Path,
    documents: Sequence[Mapping[str, object]],
    judgements: Sequence[Mapping[str, object]],
) -> CampaignAggregatesDto | None:
    """Prefer the campaign's published summary.json, else compute from run documents."""
    if not documents:
        return None
    document = _read_summary_document(directory)
    if document is None:
        results = []
        for item in documents:
            try:
                results.append(result_from_document(item))
            except (TypeError, ValueError, KeyError, AttributeError):
                continue
        if not results:
            return None
        document = summarize(
            results,
            campaign_id=_text(documents[0].get("campaign_id")),
            judgements=list(judgements),
        ).to_document()
    return CampaignAggregatesDto(
        started_runs=_count(document.get("started_runs")) or 0,
        valid_runs=_count(document.get("valid_runs")) or 0,
        strict_success_runs=_count(document.get("strict_success_runs")) or 0,
        artifact_correct_runs=_count(document.get("artifact_correct_runs")) or 0,
        task_completion_rate=_ratio(document.get("task_completion_rate")),
        robust_task_count=_count(document.get("robust_task_count")) or 0,
        total_input_tokens=_count(document.get("total_input_tokens")),
        total_output_tokens=_count(document.get("total_output_tokens")),
        total_main_requests=_count(document.get("total_main_requests")) or 0,
        total_tool_calls=_count(_mapping(document.get("tools")).get("executed")) or 0,
        judged_runs=_count(document.get("judged_runs")) or 0,
        judge_error_runs=_count(document.get("judge_error_runs")) or 0,
        judge_means={
            name: _ratio(_mapping(document.get("judge_means")).get(name)) for name in SCORE_NAMES
        },
        failure_kinds=_count_entries(document.get("failure_kinds")),
    )


def _read_summary_document(directory: Path) -> Mapping[str, object] | None:
    """Read the campaign's published summary.json, wherever summarize wrote it."""
    for relative in ("reports/summary.json", "summary.json"):
        path = directory / relative
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(document, dict) and document.get("schema_version") == SUMMARY_SCHEMA_VERSION:
            return document
    return None


def _find_run(
    documents: Sequence[Mapping[str, object]], task_id: str, repeat: int
) -> Mapping[str, object] | None:
    for document in documents:
        if document.get("task_id") == task_id and document.get("repeat") == repeat:
            return document
    return None


def _read_single_run(
    directory: Path, task_id: str, repeat: int
) -> tuple[Mapping[str, object] | None, str | None]:
    """Read one per-run run.json; an existing-but-unreadable record yields a note."""
    path = directory / "runs" / task_id / f"repeat-{repeat}" / "run.json"
    if not path.is_file():
        return None, None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema_version") != RUN_SCHEMA_VERSION:
            raise ValueError("wrong schema_version")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None, f"unreadable run record for task {task_id} repeat {repeat}"
    return document, None


def _read_single_judgement(
    directory: Path, task_id: str, repeat: int
) -> tuple[JudgementDto | None, str | None]:
    """Read one judgement.json; a corrupt record degrades to a note, never a 500."""
    path = directory / "runs" / task_id / f"repeat-{repeat}" / "judgement.json"
    if not path.is_file():
        return None, None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != JUDGEMENT_SCHEMA_VERSION
        ):
            raise ValueError("wrong schema_version")
        return JudgementDto.model_validate(document), None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, ValidationError):
        return None, f"unreadable judgement record for task {task_id} repeat {repeat}"


def _run_dto(document: Mapping[str, object]) -> tuple[RunV1Dto | None, str | None]:
    try:
        return RunV1Dto.model_validate(document), None
    except ValidationError:
        return None, "unreadable run record"


def _safe_segment(value: str) -> bool:
    """A URL segment that must name exactly one directory level, never traverse."""
    return (
        value not in {"", ".", ".."}
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
    )


def _not_found(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": code, "message": message},
    )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _count(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _ratio(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _count_entries(value: object) -> dict[str, int]:
    entries = _mapping(value)
    return {
        str(name): int(count)
        for name, count in entries.items()
        if isinstance(count, int) and not isinstance(count, bool)
    }


__all__ = ["require_browser_origin", "router"]
