from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from coding_agent.core.models import ErrorKind, StopReason
from coding_agent.evaluation.report import (
    CompactionFacts,
    DurationFacts,
    ModelFacts,
    ModificationFacts,
    OracleFacts,
    ReportError,
    RunResult,
    ToolFacts,
    UsageFacts,
    classify_failure,
    compute_artifact_correct,
    compute_strict_success,
    result_from_document,
    run_document,
    score_result,
    summarize,
    summarize_campaign,
)

SCHEMAS = Path(__file__).resolve().parents[2] / "evaluation" / "schemas"
EXAMPLES = Path(__file__).resolve().parents[2] / "evaluation" / "examples"


def published_failure_kinds() -> set[str]:
    """Return the closed failure-kind vocabulary exactly as the run schema publishes it."""
    schema = json.loads((SCHEMAS / "run-v1.schema.json").read_text(encoding="utf-8"))
    return {kind for kind in schema["properties"]["failure_kind"]["enum"] if kind is not None}


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


@pytest.fixture
def recorded_run() -> RunResult:
    """One run whose recorded facts all differ from their defaults, as the runner stores them."""
    result = RunResult(task_id="demo-task", category="local_edit", repeat=2)
    result.state = "STOPPED"
    result.stop_reason = "MAX_ROUNDS"
    result.agent_commit = "0123456789abcdef"
    result.agent_exit_code = 1
    result.started_at = "2026-08-28T12:15:53.932466+00:00"
    result.finished_at = "2026-08-28T12:15:54.182792+00:00"
    result.hashes = {
        "config": "c",
        "task": "t",
        "prompt": "p",
        "tool_schema": "s",
        "baseline_tree": "b",
        "workspace_tree": "w",
        "diff": "d",
    }
    result.model = ModelFacts(
        usage=UsageFacts(
            input_tokens=11,
            output_tokens=22,
            cache_creation_input_tokens=33,
            cache_read_input_tokens=44,
        ),
        main_requests=4,
        compaction_requests=1,
        attempts=6,
        network_retries=2,
        usage_coverage=0.75,
    )
    result.tools = ToolFacts(
        proposed=5,
        executed=4,
        succeeded=3,
        failed=1,
        skipped=1,
        duplicate_calls=2,
        output_bytes=812,
        truncated=1,
    )
    result.compaction = CompactionFacts(
        count=1,
        requests=1,
        above_target=True,
        input_tokens_before=9000,
        input_tokens_after=4000,
        estimated_summary_tokens=700,
        provider_summary_output_tokens=640,
        estimated_minus_provider_tokens=60,
    )
    result.target_oracle = OracleFacts(passed=True, exit_code=0, duration_ms=117, errored=False)
    result.regression_oracle = OracleFacts(
        passed=False, exit_code=1, duration_ms=118, errored=False
    )
    result.oracle_passed = True
    result.regressions_passed = False
    result.modifications = ModificationFacts(
        files_added=1,
        files_modified=2,
        files_deleted=1,
        lines_added=16,
        lines_removed=3,
        out_of_scope_paths=["docs/notes.md"],
    )
    result.forbidden_changes = ["README.md"]
    result.detected_workspace_escape = True
    result.durations = DurationFacts(
        workspace_prepare_ms=7,
        agent_process_ms=4,
        oracle_ms=239,
        total_ms=250,
        agent_monotonic_ms=41277,
        retry_wait_monotonic_ms=13,
        tool_execution_monotonic_ms=1904,
    )
    result.agent_report = {"schema_version": "run-report-v1", "state": "STOPPED"}
    result.model_identity = {"name": "claude-test-model-2026", "max_output_tokens": 4096}
    score_result(result)
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
    score_result(run_result)
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
    for entry in (run_result, second, third):
        score_result(entry)

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
        ("FAILED", "INTERNAL_ERROR", "INTERNAL_ERROR", "agent", "unknown"),
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


def test_no_agent_stop_reason_classifies_outside_the_published_vocabulary(
    run_result: RunResult,
) -> None:
    """The agent stop reasons are extensible; the published failure vocabulary is closed."""
    vocabulary = published_failure_kinds()
    run_result.regressions_passed = True
    run_result.state = "FAILED"

    classified = {}
    for reason in StopReason:
        run_result.stop_reason = reason.name
        classified[reason.name] = classify_failure(run_result)[1]

    assert {name: kind for name, kind in classified.items() if kind not in vocabulary} == {}


def test_internal_agent_error_scores_as_unknown_without_losing_its_stop_reason(
    run_result: RunResult,
) -> None:
    """An internal error must stay publishable while still naming the reason the run ended."""
    run_result.state = "FAILED"
    run_result.stop_reason = StopReason.INTERNAL_ERROR.name
    run_result.error_kind = ErrorKind.INTERNAL_ERROR.name
    run_result.regressions_passed = True
    run_result.agent_report = {
        "schema_version": "run-report-v1",
        "state": "FAILED",
        "stop_reason": StopReason.INTERNAL_ERROR.name,
        "error_kind": ErrorKind.INTERNAL_ERROR.name,
    }
    score_result(run_result)

    document = run_document(run_result, campaign_id="campaign-1")

    assert document["failure_kind"] in published_failure_kinds()
    assert (document["failure_stage"], document["failure_kind"]) == ("agent", "unknown")
    assert document["stop_reason"] == "INTERNAL_ERROR"
    assert document["error_kind"] == "INTERNAL_ERROR"
    assert document["agent_report"] == {
        "schema_version": "run-report-v1",
        "state": "FAILED",
        "stop_reason": "INTERNAL_ERROR",
        "error_kind": "INTERNAL_ERROR",
    }


def test_summary_names_the_model_that_produced_its_numbers(run_result: RunResult) -> None:
    """A published aggregate without a model identity cannot be compared with anything."""
    run_result.model_identity = {"name": "claude-test-model-2026"}
    second = RunResult(task_id="demo-task", category="local_edit", repeat=2)
    second.model_identity = {"name": "claude-test-model-2026"}

    summary = summarize([run_result, second])

    assert summary.model_identity == {"name": "claude-test-model-2026"}


def test_summary_model_identity_stays_null_when_runs_disagree(run_result: RunResult) -> None:
    """Naming one model for a campaign that mixed two would misattribute every number."""
    run_result.model_identity = {"name": "claude-test-model-2026"}
    second = RunResult(task_id="demo-task", category="local_edit", repeat=2)
    second.model_identity = {"name": "claude-other-model-2026"}

    summary = summarize([run_result, second])

    assert summary.model_identity is None


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


def test_reread_keeps_every_recorded_oracle_outcome(recorded_run: RunResult) -> None:
    """Back-deriving oracle results from score flags would publish an outcome never observed."""
    document = run_document(recorded_run, campaign_id="campaign-1")

    reread = result_from_document(document)

    assert reread.oracle_passed is True
    assert reread.regressions_passed is False
    assert reread.target_oracle == OracleFacts(
        passed=True, exit_code=0, duration_ms=117, errored=False
    )
    assert reread.regression_oracle == OracleFacts(
        passed=False, exit_code=1, duration_ms=118, errored=False
    )


def test_reread_reproduces_the_recorded_run_document(recorded_run: RunResult) -> None:
    """A fact dropped while re-reading would make published metrics read zeros without failing."""
    document = run_document(recorded_run, campaign_id="campaign-1")

    assert run_document(result_from_document(document), campaign_id="campaign-1") == document


def test_reread_keeps_the_recorded_compaction_and_duration_measurements(
    recorded_run: RunResult,
    tmp_path: Path,
) -> None:
    """Published summaries come from runs.jsonl, so a zeroed re-read would erase measurements."""
    document = run_document(recorded_run, campaign_id="campaign-1")
    input_dir = tmp_path / "campaign"
    input_dir.mkdir()
    (input_dir / "runs.jsonl").write_text(json.dumps(document) + "\n", encoding="utf-8")

    reread = result_from_document(document)
    summary = summarize_campaign(input_dir, tmp_path / "reports")

    assert reread.compaction == recorded_run.compaction
    assert reread.durations == recorded_run.durations
    assert summary.compaction_runs == 1
    assert summary.agent_monotonic_ms["total"] == 41277
    assert summary.total_input_tokens == 11


def test_summary_never_recomputes_a_recorded_score_flag(
    run_result: RunResult,
    tmp_path: Path,
) -> None:
    """Recomputing a score while summarizing would let a re-read overrule the immutable record."""
    passed = OracleFacts(passed=True, exit_code=0, duration_ms=5, errored=False)
    run_result.target_oracle = passed
    run_result.regression_oracle = passed
    run_result.oracle_passed = True
    run_result.regressions_passed = True
    run_result.state = "COMPLETED"
    score_result(run_result)
    document = run_document(run_result, campaign_id="campaign-1")
    document["strict_success"] = False
    document["artifact_correct"] = False
    input_dir = tmp_path / "campaign"
    input_dir.mkdir()
    (input_dir / "runs.jsonl").write_text(json.dumps(document) + "\n", encoding="utf-8")

    summary = summarize_campaign(input_dir, tmp_path / "reports")

    assert summary.strict_success_runs == 0
    assert summary.artifact_correct_runs == 0
    assert summary.tasks[0].results == (False,)


def test_summary_counts_only_the_failure_kinds_the_records_carry(
    run_result: RunResult,
    tmp_path: Path,
) -> None:
    """Re-classifying a record would publish a failure kind that scoring never assigned."""
    run_result.state = "STOPPED"
    run_result.stop_reason = "MAX_ROUNDS"
    run_result.regressions_passed = True
    score_result(run_result)
    unscored = RunResult(task_id="other-task", category="new_file", repeat=1)
    unscored.state = "STOPPED"
    unscored.stop_reason = "DOOM_LOOP"
    documents = [
        run_document(run_result, campaign_id="campaign-1"),
        run_document(unscored, campaign_id="campaign-1"),
    ]
    input_dir = tmp_path / "campaign"
    input_dir.mkdir()
    (input_dir / "runs.jsonl").write_text(
        "".join(json.dumps(document) + "\n" for document in documents),
        encoding="utf-8",
    )

    summary = summarize_campaign(input_dir, tmp_path / "reports")

    assert documents[0]["failure_kind"] == "max_rounds"
    assert documents[1]["failure_kind"] is None
    assert summary.failure_kinds == {"max_rounds": 1}


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


def test_summary_schema_closes_the_failure_kind_vocabulary() -> None:
    """An open failure map would let an unknown label be published as a fixed failure kind."""
    summary_schema = json.loads((SCHEMAS / "summary-v1.schema.json").read_text(encoding="utf-8"))
    run_schema = json.loads((SCHEMAS / "run-v1.schema.json").read_text(encoding="utf-8"))

    failure_kinds = summary_schema["properties"]["failure_kinds"]
    recorded = [
        kind for kind in run_schema["properties"]["failure_kind"]["enum"] if kind is not None
    ]
    assert failure_kinds["propertyNames"] == {"enum": recorded}
    assert failure_kinds["additionalProperties"] == {"type": "integer", "minimum": 0}


@pytest.mark.parametrize(
    "example, schema_name",
    [
        ("run-v1.redacted.json", "run-v1.schema.json"),
        ("summary-v1.json", "summary-v1.schema.json"),
    ],
)
def test_published_examples_carry_every_documented_field(example: str, schema_name: str) -> None:
    """A stale example would advertise a document shape the harness no longer writes."""
    document = json.loads((EXAMPLES / example).read_text(encoding="utf-8"))
    schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))

    assert set(document) == set(schema["properties"])
