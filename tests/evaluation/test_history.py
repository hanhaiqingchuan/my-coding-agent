"""Offline tests for the read-only campaign history index."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.evaluation import cli as evaluation_cli
from coding_agent.evaluation.history import CampaignSummary, scan_campaigns
from coding_agent.evaluation.judge import Judgement
from coding_agent.evaluation.report import RunResult, run_document, score_result


def _strict_run(
    task_id: str = "demo-task",
    repeat: int = 1,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> RunResult:
    run = RunResult(task_id=task_id, category="local_edit", repeat=repeat)
    run.oracle_passed = True
    run.regressions_passed = True
    run.state = "COMPLETED"
    run.model_identity = {"name": "claude-test-model-2026"}
    run.started_at = started_at
    run.finished_at = finished_at
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
    *,
    campaign_id: str = "campaign-1",
    started_at: str = "2026-08-28T10:00:00+00:00",
    runs: list[RunResult] | None = None,
    with_runs_jsonl: bool = True,
) -> Path:
    """Materialize one campaign directory shaped exactly like a real one."""
    directory = root / name
    directory.mkdir(parents=True)
    runs = runs if runs is not None else [_strict_run()]
    documents = [run_document(run, campaign_id=campaign_id) for run in runs]
    if with_runs_jsonl:
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


def _write_judgement(
    campaign: Path,
    task_id: str,
    repeat: int,
    *,
    scores: dict[str, int] | None = None,
    error: str | None = None,
) -> None:
    run_dir = campaign / "runs" / task_id / f"repeat-{repeat}"
    run_dir.mkdir(parents=True, exist_ok=True)
    if error is not None:
        judgement = Judgement(
            scores={},
            rationale="",
            judge_model="claude-judge-2026",
            prompt_version="judge-v1",
            error="judge_error",
            error_detail=error,
        )
    else:
        judgement = Judgement(
            scores=scores or {},
            rationale="Scored from the run facts.",
            judge_model="claude-judge-2026",
            prompt_version="judge-v1",
        )
    (run_dir / "judgement.json").write_text(
        json.dumps(
            judgement.to_document(campaign_id="campaign-1", task_id=task_id, repeat=repeat),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_scan_campaigns_on_an_empty_root_finds_nothing(tmp_path: Path) -> None:
    """An empty results root is a valid, boring history."""
    root = tmp_path / "results"
    root.mkdir()

    summaries = scan_campaigns(root)

    assert summaries == []


def test_scan_campaigns_requires_an_existing_root(tmp_path: Path) -> None:
    """A missing results root is an operator error, not an empty history."""
    from coding_agent.evaluation.history import HistoryError

    with pytest.raises(HistoryError):
        scan_campaigns(tmp_path / "absent")


def test_scan_campaigns_summarizes_one_campaign(tmp_path: Path) -> None:
    """Every headline number of one campaign is read back from its records."""
    root = tmp_path / "results"
    root.mkdir()
    _write_campaign(
        root,
        "campaign-a",
        campaign_id="campaign-a",
        runs=[
            _strict_run(
                "alpha",
                1,
                started_at="2026-08-28T10:00:00+00:00",
                finished_at="2026-08-28T10:01:00+00:00",
            ),
            _strict_run(
                "alpha",
                2,
                started_at="2026-08-28T10:05:00+00:00",
                finished_at="2026-08-28T10:06:00+00:00",
            ),
            _strict_run(
                "beta",
                1,
                started_at="2026-08-28T10:10:00+00:00",
                finished_at="2026-08-28T10:11:00+00:00",
            ),
        ],
    )

    summaries = scan_campaigns(root)

    assert len(summaries) == 1
    entry = summaries[0]
    assert entry.campaign_id == "campaign-a"
    assert entry.directory == "campaign-a"
    assert entry.started_at == "2026-08-28T10:00:00+00:00"
    assert entry.finished_at == "2026-08-28T10:11:00+00:00"
    assert entry.task_count == 2
    assert entry.started_runs == 3
    assert entry.valid_runs == 3
    assert entry.strict_success_runs == 3
    assert entry.strict_success_rate == 1.0
    assert entry.model_name == "claude-test-model-2026"
    assert entry.judged_runs == 0
    assert entry.judge_error_runs == 0
    assert entry.judge_means == {
        "task_completion": None,
        "process_quality": None,
        "communication": None,
    }
    assert entry.judge_model is None
    assert entry.corrupt is False
    assert entry.note is None


def test_scan_campaigns_reads_judgement_records(tmp_path: Path) -> None:
    """Judge means, coverage and judge errors come from the judgement records."""
    root = tmp_path / "results"
    root.mkdir()
    campaign = _write_campaign(
        root,
        "campaign-a",
        runs=[_strict_run("alpha", 1), _strict_run("alpha", 2)],
    )
    _write_judgement(
        campaign,
        "alpha",
        1,
        scores={"task_completion": 4, "process_quality": 5, "communication": 3},
    )
    _write_judgement(
        campaign, "alpha", 2, error="malformed judge response after one retry: not JSON"
    )

    summaries = scan_campaigns(root)

    entry = summaries[0]
    assert entry.judged_runs == 2
    assert entry.judge_error_runs == 1
    assert entry.judge_means == {
        "task_completion": 4.0,
        "process_quality": 5.0,
        "communication": 3.0,
    }
    assert entry.judge_model == "claude-judge-2026"


def test_scan_campaigns_counts_runs_without_runs_jsonl(tmp_path: Path) -> None:
    """A campaign is still a campaign when only per-run documents exist."""
    root = tmp_path / "results"
    root.mkdir()
    _write_campaign(root, "campaign-a", with_runs_jsonl=False)

    summaries = scan_campaigns(root)

    entry = summaries[0]
    assert entry.started_runs == 1
    assert entry.strict_success_runs == 1
    assert entry.campaign_id == "campaign-1"


def test_scan_campaigns_skips_corrupt_campaigns_with_a_note(tmp_path: Path) -> None:
    """One corrupt directory never hides the healthy campaigns around it."""
    root = tmp_path / "results"
    root.mkdir()
    _write_campaign(root, "campaign-healthy", campaign_id="campaign-healthy")
    broken = root / "campaign-broken"
    broken.mkdir()
    (broken / "runs.jsonl").write_text("{not json}\n", encoding="utf-8")

    summaries = scan_campaigns(root)

    healthy = next(entry for entry in summaries if entry.directory == "campaign-healthy")
    corrupt = next(entry for entry in summaries if entry.directory == "campaign-broken")
    assert healthy.corrupt is False
    assert healthy.started_runs == 1
    assert corrupt.corrupt is True
    assert corrupt.started_runs == 0
    assert corrupt.note is not None and "campaign-broken" not in corrupt.note


def test_scan_campaigns_ignores_directories_without_campaign_records(tmp_path: Path) -> None:
    """Scratch directories that hold no run records are not campaigns."""
    root = tmp_path / "results"
    root.mkdir()
    (root / "scratch").mkdir()
    (root / "scratch" / "notes.txt").write_text("not a campaign\n", encoding="utf-8")
    _write_campaign(root, "campaign-a")

    summaries = scan_campaigns(root)

    assert [entry.directory for entry in summaries] == ["campaign-a"]


def test_scan_campaigns_orders_campaigns_by_start_time(tmp_path: Path) -> None:
    """The index is incremental: oldest campaign first, regardless of directory name."""
    root = tmp_path / "results"
    root.mkdir()
    _write_campaign(root, "z-late", campaign_id="late", started_at="2026-08-28T12:00:00+00:00")
    _write_campaign(root, "a-early", campaign_id="early", started_at="2026-08-28T08:00:00+00:00")
    _write_campaign(root, "m-middle", campaign_id="middle", started_at="2026-08-28T10:00:00+00:00")

    summaries = scan_campaigns(root)

    assert [entry.campaign_id for entry in summaries] == ["early", "middle", "late"]


def test_scan_campaigns_is_read_only(tmp_path: Path) -> None:
    """Scanning must never write, move or delete anything under the results root."""
    root = tmp_path / "results"
    root.mkdir()
    campaign = _write_campaign(root, "campaign-a")
    before = {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}

    scan_campaigns(root)

    after = {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}
    assert before == after
    assert not list(campaign.rglob("*.tmp"))


def test_campaign_summary_exposes_the_judge_error_rate() -> None:
    """The judge error count is a first-class field, not a note."""
    entry = CampaignSummary(
        campaign_id="c",
        directory="campaign-a",
        started_at=None,
        finished_at=None,
        task_count=1,
        started_runs=3,
        valid_runs=3,
        strict_success_runs=2,
        strict_success_rate=2 / 3,
        judged_runs=3,
        judge_error_runs=1,
        judge_means={"task_completion": 4.0, "process_quality": None, "communication": None},
        model_name="claude-test-model-2026",
        judge_model="claude-judge-2026",
        corrupt=False,
        note=None,
    )

    assert entry.strict_success_rate == pytest.approx(0.666, abs=1e-3)


def test_history_command_prints_the_index_and_exits_zero(tmp_path: Path) -> None:
    """`coding-agent-eval history --results <dir>` is the documented query entry."""
    root = tmp_path / "results"
    root.mkdir()
    _write_campaign(root, "campaign-a", campaign_id="campaign-a")

    exit_code = evaluation_cli.main(["history", "--results", str(root)])

    assert exit_code == 0


def test_history_command_on_an_empty_root_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The documented empty-root invocation prints a note and stays at zero."""
    root = tmp_path / "results"
    root.mkdir()

    exit_code = evaluation_cli.main(["history", "--results", str(root)])

    assert exit_code == 0
    assert "no campaigns" in capsys.readouterr().out


def test_history_command_on_a_missing_root_fails(tmp_path: Path) -> None:
    """A missing results root is reported, not silently treated as empty."""
    exit_code = evaluation_cli.main(["history", "--results", str(tmp_path / "absent")])

    assert exit_code == 2
