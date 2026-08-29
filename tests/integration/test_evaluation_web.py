"""Offline tests for the read-only evaluation results API.

Every fixture campaign is materialized through the real producers (``run_document``,
``write_judgement``) so the JSON contract under test cannot drift from what a real
campaign writes. The endpoints are strictly read-only mirrors of that disk state.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from coding_agent.api.app import create_app
from coding_agent.evaluation.judge import (
    JUDGEMENT_SCHEMA_VERSION,
    PROMPT_VERSION,
    Judgement,
    write_judgement,
)
from coding_agent.evaluation.report import (
    OracleFacts,
    RunResult,
    run_document,
    score_result,
    summarize_campaign,
)
from coding_agent.runtime.publisher import EventPublisher
from coding_agent.storage.sqlite import SQLiteStore

SERVER_PORT = 8127
BASE_URL = f"http://127.0.0.1:{SERVER_PORT}"
ORIGIN = BASE_URL


def _make_client(tmp_path: Path, results_root: Path | None) -> TestClient:
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    app = create_app(
        store,
        None,
        {},
        event_publisher=EventPublisher(),
        server_port=SERVER_PORT,
        evaluation_results_root=results_root,
    )
    return TestClient(app, base_url=BASE_URL)


def _metrics_run(task_id: str = "demo-task", repeat: int = 1) -> RunResult:
    """One strict-success run carrying every deterministic metric the rows expose."""
    run = RunResult(task_id=task_id, category="local_edit", repeat=repeat)
    run.oracle_passed = True
    run.regressions_passed = True
    run.target_oracle = OracleFacts(passed=True, exit_code=0, duration_ms=4, errored=False)
    run.regression_oracle = OracleFacts(passed=True, exit_code=0, duration_ms=3, errored=False)
    run.state = "COMPLETED"
    run.stop_reason = "COMPLETED"
    run.model_identity = {"name": "claude-test-model-2026"}
    run.model.usage.input_tokens = 40
    run.model.usage.output_tokens = 12
    run.model.main_requests = 2
    run.tools.executed = 5
    run.tools.succeeded = 5
    run.durations.agent_monotonic_ms = 100
    run.durations.total_ms = 150
    run.hashes = {
        "config": "c",
        "task": "t",
        "prompt": "p",
        "tool_schema": "s",
        "baseline_tree": "b",
        "workspace_tree": "w",
        "diff": "d",
    }
    score_result(run)
    return run


def _write_campaign(
    root: Path,
    name: str,
    runs: list[RunResult],
    *,
    campaign_id: str = "campaign-1",
    runs_jsonl: bool = True,
    started_at: str = "2026-08-28T10:00:00+00:00",
) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    documents = []
    for run in runs:
        run.started_at = started_at
        run.finished_at = started_at
        documents.append(run_document(run, campaign_id=campaign_id))
    if runs_jsonl:
        (directory / "runs.jsonl").write_text(
            "".join(json.dumps(document) + "\n" for document in documents), encoding="utf-8"
        )
    else:
        for document in documents:
            run_dir = directory / "runs" / str(document["task_id"]) / f"repeat-{document['repeat']}"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    return directory


def _write_judgement_for(
    campaign: Path,
    task_id: str,
    repeat: int,
    *,
    scores: dict[str, int] | None = None,
    error: str | None = None,
    judge_model: str = "claude-judge-2026",
) -> None:
    run_dir = campaign / "runs" / task_id / f"repeat-{repeat}"
    run_dir.mkdir(parents=True, exist_ok=True)
    if error is not None:
        judgement = Judgement(
            scores={},
            rationale="",
            judge_model=judge_model,
            prompt_version=PROMPT_VERSION,
            error="judge_error",
            error_detail=error,
        )
    else:
        judgement = Judgement(
            scores=scores or {},
            rationale="Scored from the run facts.",
            judge_model=judge_model,
            prompt_version=PROMPT_VERSION,
        )
    write_judgement(
        run_dir / "judgement.json",
        judgement,
        campaign_id="campaign-1",
        task_id=task_id,
        repeat=repeat,
    )


# --- the campaign list ------------------------------------------------------


def test_list_campaigns_returns_summaries_sorted_newest_first(tmp_path: Path) -> None:
    """The dashboard opens on the most recent campaign, so the index leads with it."""
    root = tmp_path / "results"
    root.mkdir()
    _write_campaign(root, "old-campaign", [_metrics_run()], campaign_id="campaign-old")
    _write_campaign(
        root,
        "new-campaign",
        [_metrics_run(), _metrics_run("other-task", repeat=1)],
        campaign_id="campaign-new",
        started_at="2026-08-29T10:00:00+00:00",
    )
    client = _make_client(tmp_path, root)

    response = client.get("/api/evaluations")

    assert response.status_code == 200
    body = response.json()
    assert [entry["directory"] for entry in body] == ["new-campaign", "old-campaign"]
    newest = body[0]
    assert newest["campaign_id"] == "campaign-new"
    assert newest["task_count"] == 2
    assert newest["started_runs"] == 2
    assert newest["valid_runs"] == 2
    assert newest["strict_success_runs"] == 2
    assert newest["strict_success_rate"] == 1.0
    assert newest["model_name"] == "claude-test-model-2026"
    assert newest["corrupt"] is False
    assert newest["started_at"] == "2026-08-29T10:00:00+00:00"
    assert newest["finished_at"] == "2026-08-29T10:00:00+00:00"


def test_list_campaigns_returns_empty_when_the_results_root_is_missing(
    tmp_path: Path,
) -> None:
    """A fresh checkout has no results root yet; the dashboard shows its empty state."""
    client = _make_client(tmp_path, tmp_path / "results" / "not-created-yet")

    response = client.get("/api/evaluations")

    assert response.status_code == 200
    assert response.json() == []


def test_list_campaigns_reports_corrupt_campaigns_without_failing(tmp_path: Path) -> None:
    """A campaign with no readable records is listed as marked corrupt, never skipped."""
    root = tmp_path / "results"
    root.mkdir()
    (root / "broken").mkdir()
    (root / "broken" / "runs.jsonl").write_text("{not json\n", encoding="utf-8")
    client = _make_client(tmp_path, root)

    response = client.get("/api/evaluations")

    assert response.status_code == 200
    entry = response.json()[0]
    assert entry["directory"] == "broken"
    assert entry["corrupt"] is True
    assert entry["campaign_id"] is None
    assert entry["task_count"] == 0


def test_list_campaigns_reports_judge_means_when_records_exist(tmp_path: Path) -> None:
    """Judged campaigns surface the three judge means beside the deterministic rate."""
    root = tmp_path / "results"
    root.mkdir()
    campaign = _write_campaign(root, "judged", [_metrics_run()])
    _write_judgement_for(
        campaign,
        "demo-task",
        1,
        scores={"task_completion": 4, "process_quality": 5, "communication": 3},
    )
    client = _make_client(tmp_path, root)

    body = client.get("/api/evaluations").json()

    entry = body[0]
    assert entry["judged_runs"] == 1
    assert entry["judge_error_runs"] == 0
    assert entry["judge_model"] == "claude-judge-2026"
    assert entry["judge_means"] == {
        "task_completion": 4.0,
        "process_quality": 5.0,
        "communication": 3.0,
    }


# --- the campaign detail ----------------------------------------------------


def test_campaign_detail_rows_carry_deterministic_metrics_and_judge_scores(
    tmp_path: Path,
) -> None:
    """Each task row shows rounds, tool calls, tokens, durations, badges and scores."""
    root = tmp_path / "results"
    root.mkdir()
    campaign = _write_campaign(root, "campaign-dir", [_metrics_run()])
    _write_judgement_for(
        campaign,
        "demo-task",
        1,
        scores={"task_completion": 4, "process_quality": 5, "communication": 3},
    )
    client = _make_client(tmp_path, root)

    response = client.get("/api/evaluations/campaign-dir")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["directory"] == "campaign-dir"
    assert body["summary"]["campaign_id"] == "campaign-1"
    assert len(body["tasks"]) == 1
    task = body["tasks"][0]
    assert task["task_id"] == "demo-task"
    assert task["category"] == "local_edit"
    assert len(task["runs"]) == 1
    row = task["runs"][0]
    assert row["task_id"] == "demo-task"
    assert row["repeat"] == 1
    assert row["outcome"] == "OK"
    assert row["strict_success"] is True
    assert row["artifact_correct"] is True
    assert row["state"] == "COMPLETED"
    assert row["stop_reason"] == "COMPLETED"
    assert row["rounds"] == 2
    assert row["tool_calls"] == 5
    assert row["input_tokens"] == 40
    assert row["output_tokens"] == 12
    assert row["agent_ms"] == 100
    assert row["total_ms"] == 150
    assert row["judge_scores"] == {
        "task_completion": 4,
        "process_quality": 5,
        "communication": 3,
    }
    assert row["judge_error"] is False


def test_campaign_detail_marks_missing_and_errored_judgements(tmp_path: Path) -> None:
    """An unjudged run has null scores; a judge error is flagged, not scored."""
    root = tmp_path / "results"
    root.mkdir()
    campaign = _write_campaign(
        root,
        "mixed",
        [_metrics_run("judged-task"), _metrics_run("unjudged-task"), _metrics_run("failed-judge")],
    )
    _write_judgement_for(
        campaign,
        "judged-task",
        1,
        scores={"task_completion": 5, "process_quality": 5, "communication": 5},
    )
    _write_judgement_for(campaign, "failed-judge", 1, error="the judge request failed")
    client = _make_client(tmp_path, root)

    body = client.get("/api/evaluations/mixed").json()

    rows = {run["task_id"]: run for task in body["tasks"] for run in task["runs"]}
    assert rows["judged-task"]["judge_scores"]["task_completion"] == 5
    assert rows["unjudged-task"]["judge_scores"] is None
    assert rows["unjudged-task"]["judge_error"] is False
    assert rows["failed-judge"]["judge_scores"] is None
    assert rows["failed-judge"]["judge_error"] is True


def test_campaign_detail_aggregates_are_computed_without_a_summary_file(
    tmp_path: Path,
) -> None:
    """A campaign that was never summarized still shows honest computed aggregates."""
    root = tmp_path / "results"
    root.mkdir()
    failing = _metrics_run("failing-task")
    failing.oracle_passed = False
    failing.target_oracle = OracleFacts(passed=False, exit_code=1, duration_ms=4, errored=False)
    failing.state = "COMPLETED"
    failing.stop_reason = "COMPLETED"
    score_result(failing)
    _write_campaign(root, "computed", [_metrics_run(), failing])
    client = _make_client(tmp_path, root)

    body = client.get("/api/evaluations/computed").json()

    aggregates = body["aggregates"]
    assert aggregates["started_runs"] == 2
    assert aggregates["valid_runs"] == 2
    assert aggregates["strict_success_runs"] == 1
    assert aggregates["artifact_correct_runs"] == 1
    assert aggregates["task_completion_rate"] == 0.5
    assert aggregates["robust_task_count"] == 0
    assert aggregates["total_input_tokens"] == 80
    assert aggregates["total_output_tokens"] == 24
    assert aggregates["total_main_requests"] == 4
    assert aggregates["total_tool_calls"] == 10
    assert aggregates["failure_kinds"] == {"oracle_failure": 1}


def test_campaign_detail_aggregates_prefer_the_published_summary_json(
    tmp_path: Path,
) -> None:
    """The dashboard reads the published aggregate instead of recomputing it."""
    root = tmp_path / "results"
    root.mkdir()
    campaign = _write_campaign(root, "published", [_metrics_run()])
    summarize_campaign(campaign, campaign / "reports")
    published = json.loads((campaign / "reports" / "summary.json").read_text(encoding="utf-8"))
    published["total_main_requests"] = 999
    (campaign / "reports" / "summary.json").write_text(
        json.dumps(published, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    client = _make_client(tmp_path, root)

    body = client.get("/api/evaluations/published").json()

    assert body["aggregates"]["total_main_requests"] == 999
    assert body["aggregates"]["strict_success_runs"] == 1
    assert body["aggregates"]["judged_runs"] == 0


def test_campaign_detail_returns_404_for_unknown_campaign(tmp_path: Path) -> None:
    """Only directories the index recognizes are addressable."""
    root = tmp_path / "results"
    root.mkdir()
    _write_campaign(root, "real", [_metrics_run()])
    client = _make_client(tmp_path, root)

    missing = client.get("/api/evaluations/no-such-campaign")
    traversal = client.get("/api/evaluations/..")

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "CAMPAIGN_NOT_FOUND"
    assert traversal.status_code == 404


def test_campaign_detail_degrades_when_the_runs_index_is_corrupt(tmp_path: Path) -> None:
    """A corrupt campaign still renders, as a marked corrupt entry with no rows."""
    root = tmp_path / "results"
    root.mkdir()
    (root / "broken").mkdir()
    (root / "broken" / "runs.jsonl").write_text("{not json\n", encoding="utf-8")
    client = _make_client(tmp_path, root)

    response = client.get("/api/evaluations/broken")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["corrupt"] is True
    assert body["tasks"] == []
    assert body["aggregates"] is None


# --- one run with its judgement ---------------------------------------------


def test_run_detail_serves_the_run_document_with_its_judgement(tmp_path: Path) -> None:
    """The run page shows the verbatim run-v1 facts plus the judgement record."""
    root = tmp_path / "results"
    root.mkdir()
    campaign = _write_campaign(root, "campaign-dir", [_metrics_run()])
    _write_judgement_for(
        campaign,
        "demo-task",
        1,
        scores={"task_completion": 4, "process_quality": 5, "communication": 3},
    )
    client = _make_client(tmp_path, root)

    response = client.get("/api/evaluations/campaign-dir/runs/demo-task/1")

    assert response.status_code == 200
    body = response.json()
    assert body["campaign"] == "campaign-dir"
    assert body["task_id"] == "demo-task"
    assert body["repeat"] == 1
    run = body["run"]
    assert run["schema_version"] == "run-v1"
    assert run["campaign_id"] == "campaign-1"
    assert run["model"]["usage"]["input_tokens"] == 40
    assert run["model"]["main_requests"] == 2
    assert run["tools"]["executed"] == 5
    assert run["durations"]["agent_monotonic_ms"] == 100
    assert run["oracle"]["target"]["passed"] is True
    assert body["judgement"] == {
        "schema_version": JUDGEMENT_SCHEMA_VERSION,
        "campaign_id": "campaign-1",
        "task_id": "demo-task",
        "repeat": 1,
        "judge_model": "claude-judge-2026",
        "prompt_version": PROMPT_VERSION,
        "scores": {"task_completion": 4, "process_quality": 5, "communication": 3},
        "rationale": "Scored from the run facts.",
        "error": None,
        "error_detail": None,
    }
    assert body["judgement_note"] is None
    assert body["run_note"] is None


def test_run_detail_serves_a_run_missing_from_runs_jsonl(tmp_path: Path) -> None:
    """A per-run run.json is enough to open a run the index does not carry."""
    root = tmp_path / "results"
    root.mkdir()
    campaign = _write_campaign(root, "per-run", [_metrics_run()], runs_jsonl=False)
    _write_judgement_for(
        campaign,
        "demo-task",
        1,
        scores={"task_completion": 3, "process_quality": 3, "communication": 3},
    )
    client = _make_client(tmp_path, root)

    response = client.get("/api/evaluations/per-run/runs/demo-task/1")

    assert response.status_code == 200
    assert response.json()["run"]["task_id"] == "demo-task"
    assert response.json()["judgement"]["scores"]["task_completion"] == 3


def test_run_detail_returns_404_for_unknown_task_or_repeat(tmp_path: Path) -> None:
    """Unknown task ids and repeats are 404s, not empty payloads."""
    root = tmp_path / "results"
    root.mkdir()
    _write_campaign(root, "campaign-dir", [_metrics_run()])
    client = _make_client(tmp_path, root)

    unknown_task = client.get("/api/evaluations/campaign-dir/runs/missing-task/1")
    unknown_repeat = client.get("/api/evaluations/campaign-dir/runs/demo-task/9")
    unknown_campaign = client.get("/api/evaluations/no-such/runs/demo-task/1")
    traversal = client.get("/api/evaluations/campaign-dir/runs/..%2F..%2Fetc/1")
    empty_task = client.get("/api/evaluations/campaign-dir/runs//1")
    zero_repeat = client.get("/api/evaluations/campaign-dir/runs/demo-task/0")

    assert unknown_task.status_code == 404
    assert unknown_task.json()["detail"]["code"] == "RUN_NOT_FOUND"
    assert unknown_repeat.status_code == 404
    assert unknown_campaign.status_code == 404
    assert traversal.status_code == 404
    assert empty_task.status_code in {404, 422}
    assert zero_repeat.status_code == 404


def test_run_detail_degrades_gracefully_when_the_judgement_is_corrupt(
    tmp_path: Path,
) -> None:
    """A corrupt judgement.json becomes a null judgement with a note, never a 500."""
    root = tmp_path / "results"
    root.mkdir()
    campaign = _write_campaign(root, "campaign-dir", [_metrics_run()])
    run_dir = campaign / "runs" / "demo-task" / "repeat-1"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "judgement.json").write_text("{not json\n", encoding="utf-8")
    client = _make_client(tmp_path, root)

    response = client.get("/api/evaluations/campaign-dir/runs/demo-task/1")

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["task_id"] == "demo-task"
    assert body["judgement"] is None
    assert body["judgement_note"] is not None
    assert "judgement" in body["judgement_note"]


def test_run_detail_degrades_gracefully_when_the_run_document_is_unreadable(
    tmp_path: Path,
) -> None:
    """An unreadable per-run run.json yields null run facts with a note, never a 500."""
    root = tmp_path / "results"
    root.mkdir()
    campaign = _write_campaign(root, "campaign-dir", [_metrics_run()], runs_jsonl=False)
    run_dir = campaign / "runs" / "demo-task" / "repeat-1"
    (run_dir / "run.json").write_text("{not json\n", encoding="utf-8")
    client = _make_client(tmp_path, root)

    response = client.get("/api/evaluations/campaign-dir/runs/demo-task/1")

    assert response.status_code == 200
    body = response.json()
    assert body["run"] is None
    assert body["run_note"] is not None
    assert body["judgement"] is None


# --- the browser trust boundary ---------------------------------------------


def test_evaluation_reads_enforce_loopback_host_and_allowed_origin(tmp_path: Path) -> None:
    """Read-only still means browser-only: Host must be loopback and Origin, if sent, ours."""
    root = tmp_path / "results"
    root.mkdir()
    _write_campaign(root, "campaign-dir", [_metrics_run()])
    client = _make_client(tmp_path, root)

    wrong_host = client.get("/api/evaluations", headers={"Host": "attacker.invalid:8127"})
    wrong_port = client.get("/api/evaluations", headers={"Host": "127.0.0.1:8128"})
    cross_origin = client.get("/api/evaluations", headers={"Origin": "https://attacker.invalid"})
    cross_origin_detail = client.get(
        "/api/evaluations/campaign-dir",
        headers={"Origin": "https://attacker.invalid"},
    )
    same_origin = client.get("/api/evaluations", headers={"Origin": ORIGIN})
    no_origin = client.get("/api/evaluations")

    assert wrong_host.status_code == 400
    assert wrong_port.status_code == 400
    assert cross_origin.status_code == 403
    assert cross_origin.json()["detail"]["code"] == "ORIGIN_FORBIDDEN"
    assert cross_origin_detail.status_code == 403
    assert same_origin.status_code == 200
    assert no_origin.status_code == 200


def test_evaluation_responses_expose_no_absolute_paths_or_credentials(
    tmp_path: Path,
) -> None:
    """The viewer serves redacted documents only: no local paths, no secrets."""
    root = tmp_path / "results"
    root.mkdir()
    campaign = _write_campaign(root, "campaign-dir", [_metrics_run()])
    _write_judgement_for(
        campaign,
        "demo-task",
        1,
        scores={"task_completion": 4, "process_quality": 4, "communication": 4},
    )
    client = _make_client(tmp_path, root)

    combined = "".join(
        response.text
        for response in (
            client.get("/api/evaluations"),
            client.get("/api/evaluations/campaign-dir"),
            client.get("/api/evaluations/campaign-dir/runs/demo-task/1"),
        )
    )

    assert str(tmp_path) not in combined
    assert str(root) not in combined
    assert "secret" not in combined.lower()
    assert "api_key" not in combined.lower()


def test_frontend_evaluation_fixture_matches_the_backend_dtos() -> None:
    """The checked-in browser fixture is real producer output and must track the DTOs."""
    from coding_agent.evaluation.web import (
        CampaignDetailDto,
        CampaignSummaryDto,
        RunDetailDto,
    )

    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "web"
        / "src"
        / "features"
        / "evaluation"
        / "fixtures"
        / "evaluation.fixture.json"
    )
    raw = fixture_path.read_text(encoding="utf-8")
    fixture = json.loads(raw)

    summaries = [CampaignSummaryDto.model_validate(entry) for entry in fixture["campaigns"]]
    detail = CampaignDetailDto.model_validate(fixture["campaignDetail"])
    run = RunDetailDto.model_validate(fixture["runDetail"])

    assert len(summaries) == 2
    assert summaries[0].directory == "plain-campaign"
    assert detail.summary.directory == "judged-campaign"
    assert [task.task_id for task in detail.tasks] == ["demo-task", "second-task"]
    assert detail.aggregates is not None
    assert detail.aggregates.judged_runs == 2
    assert run.run is not None
    assert run.run.agent_report["final_assistant_text"]
    assert run.judgement is not None
    assert run.judgement.scores["task_completion"] == 4
    for leak in ("/var/folders", "/Users/", "/private/", "api_key", "secret"):
        assert leak not in raw
