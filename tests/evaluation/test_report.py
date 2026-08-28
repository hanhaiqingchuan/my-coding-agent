from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from coding_agent.evaluation.report import (
    ReportError,
    RunResult,
    classify_failure,
    compute_artifact_correct,
    compute_strict_success,
    run_document,
    summarize,
    summarize_campaign,
)

SCHEMAS = Path(__file__).resolve().parents[2] / "evaluation" / "schemas"


@pytest.fixture
def run_result() -> RunResult:
    result = RunResult(task_id="demo-task", category="local_edit", repeat=1)
    result.model.usage.input_tokens = 40
    result.model.usage.output_tokens = 12
    result.model.usage.cache_creation_input_tokens = 0
    result.model.usage.cache_read_input_tokens = 0
    result.model.main_requests = 2
    result.model.attempts = 2
    result.model.usage_coverage = 1.0
    result.durations.agent_monotonic_ms = 100
    result.hashes = {
        "config": "c",
        "task": "t",
        "prompt": "p",
        "tool_schema": "s",
        "baseline_tree": "b",
        "workspace_tree": "w",
        "diff": "d",
    }
    return result


def test_strict_success_requires_all_conditions(run_result: RunResult) -> None:
    """Reporting success without every condition would overstate the agent's capability."""
    run_result.oracle_passed = True
    run_result.regressions_passed = True
    run_result.forbidden_changes = []
    run_result.detected_workspace_escape = False
    run_result.state = "COMPLETED"

    assert compute_strict_success(run_result) is True


@pytest.mark.parametrize(
    "field, value",
    [
        ("oracle_passed", False),
        ("regressions_passed", False),
        ("forbidden_changes", ["README.md"]),
        ("detected_workspace_escape", True),
        ("state", "STOPPED"),
        ("outcome", "HARNESS_SETUP"),
    ],
)
def test_strict_success_fails_when_any_condition_is_missing(
    run_result: RunResult,
    field: str,
    value: object,
) -> None:
    """Each condition guards a different way a run can look successful but not be."""
    run_result.oracle_passed = True
    run_result.regressions_passed = True
    run_result.state = "COMPLETED"
    setattr(run_result, field, value)

    assert compute_strict_success(run_result) is False


def test_artifact_correct_is_reported_separately_from_run_control(
    run_result: RunResult,
) -> None:
    """Mixing artifact quality with control quality would hide why a run failed."""
    run_result.oracle_passed = True
    run_result.regressions_passed = True
    run_result.state = "STOPPED"
    run_result.stop_reason = "MAX_ROUNDS"

    assert compute_artifact_correct(run_result) is True
    assert compute_strict_success(run_result) is False


def test_missing_usage_remains_null(run_result: RunResult) -> None:
    """Substituting a local estimate for provider usage would falsify cost reporting."""
    run_result.model.usage.input_tokens = None

    summary = summarize([run_result])

    assert summary.total_input_tokens is None
    assert summary.total_output_tokens == 12


def test_harness_outcomes_stay_out_of_the_capability_denominator(
    run_result: RunResult,
) -> None:
    """Counting harness defects as agent failures would understate real capability."""
    run_result.oracle_passed = True
    run_result.regressions_passed = True
    run_result.state = "COMPLETED"
    setup = RunResult(task_id="setup-task", category="new_file", repeat=1, outcome="HARNESS_SETUP")
    oracle = RunResult(
        task_id="oracle-task",
        category="new_file",
        repeat=1,
        outcome="HARNESS_ORACLE_ERROR",
    )

    summary = summarize([run_result, setup, oracle])

    assert summary.started_runs == 3
    assert summary.valid_runs == 1
    assert summary.harness_setup_runs == 1
    assert summary.harness_oracle_error_runs == 1
    assert summary.strict_success_runs == 1
    assert summary.task_completion_rate == 1.0


def test_task_completion_rate_is_none_without_any_valid_run() -> None:
    """A zero denominator must stay undefined instead of collapsing to zero percent."""
    summary = summarize(
        [RunResult(task_id="a", category="new_file", repeat=1, outcome="HARNESS_SETUP")]
    )

    assert summary.valid_runs == 0
    assert summary.task_completion_rate is None
    assert summary.robust_task_count == 0


def test_robust_tasks_require_a_majority_of_repeats(run_result: RunResult) -> None:
    """Averaging away individual repeats would hide unstable tasks."""
    run_result.oracle_passed = True
    run_result.regressions_passed = True
    run_result.state = "COMPLETED"
    second = RunResult(task_id="demo-task", category="local_edit", repeat=2)
    second.oracle_passed = True
    second.regressions_passed = True
    second.state = "COMPLETED"
    third = RunResult(task_id="demo-task", category="local_edit", repeat=3)

    summary = summarize([run_result, second, third])

    assert summary.robust_task_count == 1
    assert summary.tasks[0].results == (True, True, False)
    assert summary.tasks[0].strict_success_runs == 2


@pytest.mark.parametrize(
    "state, stop_reason, error_kind, stage, kind",
    [
        ("COMPLETED", "COMPLETED", None, "oracle", "oracle_failure"),
        ("FAILED", "AUTH_ERROR", "AUTH_ERROR", "model", "model_auth"),
        ("FAILED", "RETRY_EXHAUSTED", "RETRY_EXHAUSTED", "model", "model_transport"),
        (
            "FAILED",
            "MODEL_PROTOCOL_ERROR",
            "MODEL_PROTOCOL_ERROR",
            "model",
            "model_protocol",
        ),
        ("FAILED", "CONTEXT_OVERFLOW", "CONTEXT_OVERFLOW", "agent", "context_overflow"),
        ("STOPPED", "MODEL_REFUSAL", None, "model", "model_refusal"),
        ("STOPPED", "PAUSE_TURN", None, "model", "pause_turn"),
        ("STOPPED", "OUTPUT_TRUNCATED", None, "model", "output_truncated"),
        ("STOPPED", "MAX_ROUNDS", None, "agent", "max_rounds"),
        ("STOPPED", "DOOM_LOOP", None, "agent", "doom_loop"),
        ("FAILED", "CONFIG_ERROR", "CONFIG_ERROR", "setup", "harness_setup"),
        ("CANCELLED", "USER_STOP", None, "agent", "unknown"),
    ],
)
def test_failure_classification_uses_the_fixed_vocabulary(
    run_result: RunResult,
    state: str,
    stop_reason: str,
    error_kind: str | None,
    stage: str,
    kind: str,
) -> None:
    """Free-form failure labels would make cross-run failure counts meaningless."""
    run_result.state = state
    run_result.stop_reason = stop_reason
    run_result.error_kind = error_kind
    run_result.regressions_passed = True

    assert classify_failure(run_result) == (stage, kind)


def test_successful_run_has_no_failure_classification(run_result: RunResult) -> None:
    """A strict success carrying a failure stage would corrupt failure aggregation."""
    run_result.oracle_passed = True
    run_result.regressions_passed = True
    run_result.state = "COMPLETED"

    assert classify_failure(run_result) == (None, None)


def test_run_document_matches_the_published_schema_and_hides_raw_inputs(
    run_result: RunResult,
) -> None:
    """A published run record must stay both schema-stable and free of task content."""
    run_result.oracle_passed = True
    run_result.regressions_passed = True
    run_result.state = "COMPLETED"
    run_result.agent_commit = "0123456789abcdef"
    run_result.strict_success = compute_strict_success(run_result)
    run_result.artifact_correct = compute_artifact_correct(run_result)

    document = run_document(run_result, campaign_id="campaign-1")
    schema = json.loads((SCHEMAS / "run-v1.schema.json").read_text(encoding="utf-8"))

    assert document["schema_version"] == "run-v1"
    assert document["provider"] == "anthropic_messages"
    assert set(schema["required"]) <= set(document)
    assert set(document) == set(schema["properties"])
    assert document["hashes"]["prompt"] == "p"
    assert not {"prompt_text", "transcript", "tool_arguments", "workspace"} & set(document)


def test_run_and_summary_documents_agree_on_every_number(
    run_result: RunResult,
    tmp_path: Path,
) -> None:
    """Divergent totals between run and summary exports would make results unauditable."""
    run_result.oracle_passed = True
    run_result.regressions_passed = True
    run_result.state = "COMPLETED"
    run_result.strict_success = True
    run_result.artifact_correct = True
    input_dir = tmp_path / "campaign"
    input_dir.mkdir()
    (input_dir / "runs.jsonl").write_text(
        json.dumps(run_document(run_result, campaign_id="campaign-1")) + "\n",
        encoding="utf-8",
    )

    summary = summarize_campaign(input_dir, tmp_path / "reports")

    document = json.loads((tmp_path / "reports" / "summary.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader((tmp_path / "reports" / "summary.csv").open(encoding="utf-8")))
    assert document["schema_version"] == "summary-v1"
    assert document["total_input_tokens"] == summary.total_input_tokens == 40
    assert document["total_output_tokens"] == summary.total_output_tokens == 12
    assert document["strict_success_runs"] == summary.strict_success_runs == 1
    assert document["valid_runs"] == summary.valid_runs == 1
    assert rows[0]["input_tokens"] == "40"
    assert rows[0]["strict_success"] == "True"
    assert (tmp_path / "reports" / "report.md").exists()


def test_summarize_campaign_refuses_to_overwrite_an_existing_report(
    run_result: RunResult,
    tmp_path: Path,
) -> None:
    """Overwriting a published summary would silently rewrite reported results."""
    input_dir = tmp_path / "campaign"
    input_dir.mkdir()
    (input_dir / "runs.jsonl").write_text(
        json.dumps(run_document(run_result, campaign_id="campaign-1")) + "\n",
        encoding="utf-8",
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "summary.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ReportError, match="already"):
        summarize_campaign(input_dir, reports)


def test_summarize_campaign_rejects_an_unknown_run_schema(tmp_path: Path) -> None:
    """Summarizing an unknown run version would mix incompatible field meanings."""
    input_dir = tmp_path / "campaign"
    input_dir.mkdir()
    (input_dir / "runs.jsonl").write_text(
        json.dumps({"schema_version": "run-v2"}) + "\n", encoding="utf-8"
    )

    with pytest.raises(ReportError, match="run-v1"):
        summarize_campaign(input_dir, tmp_path / "reports")


def test_summary_document_matches_the_published_schema(run_result: RunResult) -> None:
    """A summary that drifts from its schema cannot be consumed by the report tooling."""
    summary = summarize([run_result], campaign_id="campaign-1", agent_commit="0123456789abcdef")
    schema = json.loads((SCHEMAS / "summary-v1.schema.json").read_text(encoding="utf-8"))

    document = summary.to_document()

    assert set(schema["required"]) <= set(document)
    assert set(document) == set(schema["properties"])
