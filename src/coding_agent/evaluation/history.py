"""The read-only campaign history index.

Scanning never writes, moves or deletes anything under the results root, and it is a
pure function of what is on disk, so callers may cache its result freely. A campaign
directory is any subdirectory holding a ``runs.jsonl`` or at least one
``runs/*/run.json`` record; a directory whose records cannot be read is reported as a
corrupt entry with a note instead of being skipped silently or failing the scan.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from coding_agent.evaluation.judge import JUDGE_ERROR, SCORE_NAMES
from coding_agent.evaluation.report import OUTCOME_OK, RUN_SCHEMA_VERSION

JUDGEMENT_SCHEMA_VERSION = "judgement-v1"


class HistoryError(ValueError):
    """Raised when a results root cannot be scanned at all."""


@dataclass(frozen=True, slots=True)
class CampaignSummary:
    """The headline numbers of one scanned campaign directory."""

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
    judge_means: Mapping[str, float | None]
    model_name: str | None
    judge_model: str | None
    corrupt: bool
    note: str | None


def scan_campaigns(results_root: Path) -> list[CampaignSummary]:
    """Index every campaign directory under ``results_root``, oldest first.

    The scan is read-only and tolerant: a corrupt campaign is reported as a marked
    entry with a note, and directories that hold no campaign records are not campaigns.
    """
    if not results_root.is_dir():
        raise HistoryError(f"results: no such directory: {results_root}")
    summaries: list[CampaignSummary] = []
    for directory in sorted(path for path in results_root.iterdir() if path.is_dir()):
        if not _is_campaign_directory(directory):
            continue
        summaries.append(_summarize_campaign(directory))
    return sorted(summaries, key=lambda entry: (entry.started_at or "", entry.directory))


def format_history(summaries: Sequence[CampaignSummary]) -> tuple[str, ...]:
    """Render the index as operator-facing lines."""
    if not summaries:
        return ("no campaigns found",)
    lines: list[str] = []
    for entry in summaries:
        lines.append(
            f"campaign {entry.campaign_id or '(unknown)'} [{entry.directory}]"
            + (" (corrupt)" if entry.corrupt else "")
        )
        if entry.corrupt:
            lines.append(f"  skipped: {entry.note}")
            continue
        window = f"{entry.started_at or 'unknown'} -> {entry.finished_at or 'unknown'}"
        lines.append(f"  {window}")
        lines.append(
            f"  tasks: {entry.task_count}, runs: {entry.started_runs} "
            f"({entry.valid_runs} valid), strict success: {entry.strict_success_runs} "
            f"({_percent(entry.strict_success_rate)})"
        )
        model = f"model: {entry.model_name or 'unknown'}"
        if entry.judged_runs:
            means = ", ".join(
                f"{name} {_number(entry.judge_means.get(name))}" for name in SCORE_NAMES
            )
            model += f", judge: {entry.judge_model or 'unknown'}"
            lines.append(
                f"  {model} | judged: {entry.judged_runs} "
                f"({entry.judge_error_runs} judge errors), judge means: {means}"
            )
        else:
            lines.append(f"  {model}")
    return tuple(lines)


def read_run_documents(directory: Path) -> tuple[list[dict[str, object]], str | None]:
    """Read one campaign's run documents tolerantly (``runs.jsonl`` first).

    Read-only consumers such as the results web API reuse this exact strategy so a
    partially corrupt record degrades to a note instead of failing the request.
    """
    return _read_run_documents(directory)


def read_judgements(directory: Path) -> tuple[list[dict[str, object]], str | None]:
    """Read one campaign's judgement records tolerantly, skipping unreadable ones."""
    return _read_judgements(directory)


def _is_campaign_directory(directory: Path) -> bool:
    """A campaign holds a runs.jsonl or at least one per-run run.json record."""
    if (directory / "runs.jsonl").is_file():
        return True
    return any(directory.glob("runs/*/*/run.json"))


def _summarize_campaign(directory: Path) -> CampaignSummary:
    documents, note = _read_run_documents(directory)
    judgements, judgement_note = _read_judgements(directory)
    if not documents:
        detail = note or "no run records found"
        if judgement_note:
            detail = f"{detail}; {judgement_note}"
        return _corrupt_entry(directory, detail)
    started_runs = len(documents)
    valid = [item for item in documents if item.get("outcome") == OUTCOME_OK]
    strict = [item for item in valid if item.get("strict_success")]
    scored = [item for item in judgements if item.get("error") != JUDGE_ERROR]
    detail_parts = [part for part in (note, judgement_note) if part]
    return CampaignSummary(
        campaign_id=_first_string(documents, "campaign_id"),
        directory=directory.name,
        started_at=_extreme_string(documents, "started_at", minimum=True),
        finished_at=_extreme_string(documents, "finished_at", minimum=False),
        task_count=len({item.get("task_id") for item in documents}),
        started_runs=started_runs,
        valid_runs=len(valid),
        strict_success_runs=len(strict),
        strict_success_rate=(len(strict) / len(valid)) if valid else None,
        judged_runs=len(judgements),
        judge_error_runs=sum(1 for item in judgements if item.get("error") == JUDGE_ERROR),
        judge_means=_judge_means(scored),
        model_name=_agreed_model_name(documents),
        judge_model=_agreed_judge_model(judgements),
        corrupt=False,
        note="; ".join(detail_parts) or None,
    )


def _corrupt_entry(directory: Path, detail: str) -> CampaignSummary:
    return CampaignSummary(
        campaign_id=None,
        directory=directory.name,
        started_at=None,
        finished_at=None,
        task_count=0,
        started_runs=0,
        valid_runs=0,
        strict_success_runs=0,
        strict_success_rate=None,
        judged_runs=0,
        judge_error_runs=0,
        judge_means={name: None for name in SCORE_NAMES},
        model_name=None,
        judge_model=None,
        corrupt=True,
        note=detail,
    )


def _read_run_documents(directory: Path) -> tuple[list[dict[str, object]], str | None]:
    """Read run documents from runs.jsonl, falling back to per-run run.json files."""
    documents: list[dict[str, object]] = []
    note: str | None = None
    lines_file = directory / "runs.jsonl"
    if lines_file.is_file():
        try:
            for line in lines_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                document = json.loads(line)
                _require_run_document(document, "runs.jsonl")
                documents.append(document)
        except (OSError, UnicodeError, json.JSONDecodeError, _RecordError) as error:
            note = f"unreadable runs.jsonl ({error})" if not documents else str(error)
            documents = []
    if documents:
        return documents, note
    for path in sorted(directory.glob("runs/*/*/run.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            _require_run_document(document, path.name)
        except (OSError, UnicodeError, json.JSONDecodeError, _RecordError):
            continue
        documents.append(document)
    return documents, note


def _read_judgements(directory: Path) -> tuple[list[dict[str, object]], str | None]:
    """Read judgement records, skipping unreadable ones with a note."""
    judgements: list[dict[str, object]] = []
    skipped = 0
    for path in sorted(directory.glob("runs/*/*/judgement.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or (
                document.get("schema_version") != JUDGEMENT_SCHEMA_VERSION
            ):
                raise _RecordError("wrong schema_version")
        except (OSError, UnicodeError, json.JSONDecodeError, _RecordError):
            skipped += 1
            continue
        judgements.append(document)
    note = f"skipped {skipped} unreadable judgement record(s)" if skipped else None
    return judgements, note


class _RecordError(ValueError):
    """Internal marker for a record that is not the schema it claims to be."""


def _require_run_document(document: object, source: str) -> None:
    if not isinstance(document, dict) or document.get("schema_version") != RUN_SCHEMA_VERSION:
        raise _RecordError(f"{source}: expected schema_version {RUN_SCHEMA_VERSION}")


def _judge_means(scored: Sequence[Mapping[str, object]]) -> dict[str, float | None]:
    means: dict[str, float | None] = {}
    for name in SCORE_NAMES:
        values: list[float] = []
        for document in scored:
            value = _mapping(document.get("scores")).get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                values.append(float(value))
        means[name] = (sum(values) / len(values)) if values else None
    return means


def _agreed_model_name(documents: Sequence[Mapping[str, object]]) -> str | None:
    names = [
        _mapping(item.get("model_identity")).get("name")
        for item in documents
        if isinstance(_mapping(item.get("model_identity")).get("name"), str)
    ]
    if not names or any(name != names[0] for name in names[1:]):
        return None
    return names[0] if isinstance(names[0], str) else None


def _agreed_judge_model(judgements: Sequence[Mapping[str, object]]) -> str | None:
    names = [
        item.get("judge_model") for item in judgements if isinstance(item.get("judge_model"), str)
    ]
    if not names or any(name != names[0] for name in names[1:]):
        return None
    return names[0] if isinstance(names[0], str) else None


def _first_string(documents: Sequence[Mapping[str, object]], field: str) -> str | None:
    for item in documents:
        value = item.get(field)
        if isinstance(value, str):
            return value
    return None


def _extreme_string(
    documents: Sequence[Mapping[str, object]], field: str, *, minimum: bool
) -> str | None:
    """Return the earliest (or latest) recorded timestamp; ISO strings sort in time order."""
    values = [item.get(field) for item in documents if isinstance(item.get(field), str)]
    if not values:
        return None
    return min(values) if minimum else max(values)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


__all__ = [
    "CampaignSummary",
    "HistoryError",
    "format_history",
    "read_judgements",
    "read_run_documents",
    "scan_campaigns",
]
