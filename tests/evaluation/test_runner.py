from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from coding_agent.evaluation.manifest import validate_manifest
from coding_agent.evaluation.runner import (
    AGENT_ARGV_OPTIONS,
    AgentInvocation,
    AgentProcessResult,
    CampaignError,
    launch_agent,
    resolve_agent_executable,
    run_campaign,
    run_oracle,
    verify_task_setup,
)
from coding_agent.main import load_command_policy
from tests.evaluation.conftest import CRASHING_ORACLE, task_table, write_manifest, write_task_tree

PUBLIC_MANIFEST = Path(__file__).resolve().parents[2] / "evaluation" / "tasks" / "public"


def _agent_report(**overrides: object) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": "run-report-v1",
        "run_id": "run-1",
        "session_id": "session-1",
        "state": "COMPLETED",
        "stop_reason": "COMPLETED",
        "error_kind": None,
        "started_at": "2026-08-27T00:00:00+00:00",
        "finished_at": "2026-08-27T00:00:01+00:00",
        "tool_schema_hash": "abc123",
        "model_identity": {
            "name": "claude-stub-model-2026",
            "context_window": 64000,
            "max_output_tokens": 8192,
            "stream": True,
        },
        "model": {
            "main": {
                "requests": 2,
                "attempts": 2,
                "network_retries": 0,
                "usage_coverage": 1.0,
                "elapsed_ms": 5,
                "usage": {
                    "input_tokens": 30,
                    "output_tokens": 9,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
            "compaction": {
                "requests": 0,
                "attempts": 0,
                "network_retries": 0,
                "usage_coverage": None,
                "elapsed_ms": 0,
                "usage": {
                    "input_tokens": None,
                    "output_tokens": None,
                    "cache_creation_input_tokens": None,
                    "cache_read_input_tokens": None,
                },
            },
        },
        "tools": {
            "proposed": 1,
            "executed": 1,
            "succeeded": 1,
            "failed": 0,
            "rejected": 0,
            "cancelled": 0,
            "skipped": 0,
            "unknown": 0,
            "duplicate_calls": 0,
            "output_bytes": 4,
            "truncated": 0,
            "by_name": {"write_file": {"proposed": 1, "succeeded": 1, "failed": 0}},
            "calls": [],
        },
        "compaction": {
            "count": 0,
            "requests": 0,
            "above_target": False,
            "estimator_id": None,
            "input_tokens_before": None,
            "input_tokens_after": None,
            "estimate_error": {
                "estimated_summary_tokens": None,
                "provider_summary_output_tokens": None,
                "estimated_minus_provider_tokens": None,
            },
        },
        "durations": {
            "agent_monotonic_ms": 11,
            "retry_wait_monotonic_ms": 0,
            "tool_execution_monotonic_ms": 4,
            "model_request_elapsed_ms": 5,
            "compaction_request_elapsed_ms": 0,
        },
    }
    report.update(overrides)
    return report


class RecordingLauncher:
    """Stand in for the agent process while keeping the public argv contract intact."""

    def __init__(self, *, mutate=None, exit_code: int = 0, write_report: bool = True) -> None:
        self.invocations: list[AgentInvocation] = []
        self.workspace_snapshots: list[dict[str, str]] = []
        self._mutate = mutate
        self._exit_code = exit_code
        self._write_report = write_report

    def __call__(self, invocation: AgentInvocation) -> AgentProcessResult:
        self.invocations.append(invocation)
        self.workspace_snapshots.append(
            {
                str(path.relative_to(invocation.workspace)): path.read_text(encoding="utf-8")
                for path in sorted(invocation.workspace.rglob("*"))
                if path.is_file()
            }
        )
        if self._mutate is not None:
            self._mutate(invocation)
        if self._write_report:
            invocation.report_out.write_text(json.dumps(_agent_report()), encoding="utf-8")
        return AgentProcessResult(exit_code=self._exit_code, timed_out=False)


def _apply_gold(invocation: AgentInvocation) -> None:
    (invocation.workspace / "src" / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")


def _manifest(manifest_root: Path, **kwargs: object) -> object:
    return validate_manifest(write_manifest(manifest_root, **kwargs))


def test_verify_task_setup_accepts_failing_baseline_and_passing_gold(
    manifest_root: Path,
    tmp_path: Path,
) -> None:
    """Without this gate an already-passing baseline would score as agent success."""
    manifest = _manifest(manifest_root)

    verification = verify_task_setup(manifest.tasks[0], scratch=tmp_path / "scratch")

    assert verification.ok is True
    assert verification.baseline_failed is True
    assert verification.gold_passed is True
    assert verification.gold_regression_passed is True
    assert verification.error_variant_failed is True


def test_verify_task_setup_rejects_a_baseline_that_already_passes(
    manifest_root: Path,
    tmp_path: Path,
) -> None:
    """A baseline that satisfies the oracle makes the task incapable of measuring work."""
    (manifest_root / "demo-task" / "baseline" / "src" / "mod.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )
    manifest = _manifest(manifest_root)

    verification = verify_task_setup(manifest.tasks[0], scratch=tmp_path / "scratch")

    assert verification.ok is False
    assert verification.baseline_failed is False
    assert "baseline" in (verification.detail or "")


def test_verify_task_setup_rejects_a_gold_overlay_that_does_not_pass(
    manifest_root: Path,
    tmp_path: Path,
) -> None:
    """An unverified gold patch means the oracle may be impossible to satisfy."""
    (manifest_root / "demo-task" / "gold" / "src" / "mod.py").write_text(
        "VALUE = 9\n", encoding="utf-8"
    )
    manifest = _manifest(manifest_root)

    verification = verify_task_setup(manifest.tasks[0], scratch=tmp_path / "scratch")

    assert verification.ok is False
    assert verification.gold_passed is False


def test_verify_task_setup_rejects_an_error_variant_that_passes(
    manifest_root: Path,
    tmp_path: Path,
) -> None:
    """An oracle that accepts a wrong implementation cannot detect regressions."""
    (manifest_root / "demo-task" / "error" / "src" / "mod.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )
    manifest = _manifest(manifest_root)

    verification = verify_task_setup(manifest.tasks[0], scratch=tmp_path / "scratch")

    assert verification.ok is False
    assert verification.error_variant_failed is False


def test_verify_task_setup_reports_a_crashing_oracle_as_a_harness_problem(
    manifest_root: Path,
    tmp_path: Path,
) -> None:
    """A crashing oracle is a harness defect and must never be scored as agent failure."""
    (manifest_root / "demo-task" / "oracle" / "target.py").write_text(
        CRASHING_ORACLE, encoding="utf-8"
    )
    manifest = _manifest(manifest_root)

    verification = verify_task_setup(manifest.tasks[0], scratch=tmp_path / "scratch")

    assert verification.ok is False
    assert verification.oracle_errored is True


def test_run_oracle_never_executes_inside_the_agent_workspace(
    manifest_root: Path,
    tmp_path: Path,
) -> None:
    """An oracle running in the workspace would let the agent tamper with its own grade."""
    entry = tmp_path / "marker_oracle.py"
    entry.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "Path('marker.txt').write_text('ran', encoding='utf-8')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    oracle_cwd = tmp_path / "oracle-cwd"
    oracle_cwd.mkdir()

    outcome = run_oracle(entry, workspace, cwd=oracle_cwd)

    assert outcome.passed is True
    assert outcome.errored is False
    assert (oracle_cwd / "marker.txt").exists() is True
    assert (workspace / "marker.txt").exists() is False


def test_each_repeat_uses_a_fresh_workspace_and_isolated_data_dir(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """Reusing a workspace or database would leak one repeat's state into the next."""
    manifest = _manifest(manifest_root)
    launcher = RecordingLauncher(mutate=_apply_gold)

    result = run_campaign(
        manifest,
        config_file,
        2,
        tmp_path / "out",
        False,
        agent_launcher=launcher,
        agent_executable=("fake-agent",),
    )

    workspaces = [invocation.workspace for invocation in launcher.invocations]
    data_dirs = [invocation.data_dir for invocation in launcher.invocations]
    assert len(result.runs) == 2
    assert len(set(workspaces)) == 2
    assert len(set(data_dirs)) == 2
    assert [invocation.repeat for invocation in launcher.invocations] == [1, 2]
    for snapshot in launcher.workspace_snapshots:
        assert snapshot["src/mod.py"] == "VALUE = 1\n"
        assert not any("oracle" in name or "gold" in name for name in snapshot)
        assert "prompt.md" not in snapshot


def test_agent_argv_matches_the_frozen_headless_contract(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """Any argv drift would evaluate a path that operators never actually run."""
    manifest = _manifest(manifest_root)
    launcher = RecordingLauncher(mutate=_apply_gold)

    run_campaign(
        manifest,
        config_file,
        1,
        tmp_path / "out",
        False,
        agent_launcher=launcher,
        agent_executable=("fake-agent",),
    )

    invocation = launcher.invocations[0]
    assert invocation.argv == (
        "fake-agent",
        "run",
        "--config",
        str(config_file),
        "--workspace",
        str(invocation.workspace),
        "--data-dir",
        str(invocation.data_dir),
        "--prompt-file",
        str(invocation.prompt_file),
        "--report-out",
        str(invocation.report_out),
        "--yes",
        "--ack-unsafe-auto-approve",
        "--command-policy",
        str(invocation.command_policy),
    )
    assert AGENT_ARGV_OPTIONS == (
        "--config",
        "--workspace",
        "--data-dir",
        "--prompt-file",
        "--report-out",
        "--yes",
        "--ack-unsafe-auto-approve",
        "--command-policy",
    )


def test_resolved_agent_executable_is_the_public_entry_point() -> None:
    """Driving anything but the shipped entry point would evaluate untested code."""
    executable = resolve_agent_executable()

    assert executable in {
        (str(Path(sys.executable).parent / "coding-agent"),),
        (sys.executable, "-m", "coding_agent.cli"),
    }


def test_generated_command_policy_matches_the_manifest_allowlist(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """A generated policy the product rejects would silently disable all effects."""
    manifest = _manifest(
        manifest_root,
        tasks=task_table(
            "demo-task",
            manifest_root,
            overrides={"commands": [{"command": "python3 -V", "cwd": "src"}]},
        ),
    )
    launcher = RecordingLauncher(mutate=_apply_gold)

    run_campaign(
        manifest,
        config_file,
        1,
        tmp_path / "out",
        False,
        agent_launcher=launcher,
        agent_executable=("fake-agent",),
    )

    invocation = launcher.invocations[0]
    raw = json.loads(invocation.command_policy.read_text(encoding="utf-8"))
    policy = load_command_policy(invocation.command_policy, invocation.workspace)
    assert raw["schema_version"] == "command-policy-v1"
    assert raw["allowed"] == [{"command": "python3 -V", "cwd": "src"}]
    assert policy.allows("python3 -V", Path("src")) is True
    assert policy.allows("python3 -V", Path(".")) is False
    assert invocation.command_policy.parent != invocation.workspace


def test_dry_run_reports_the_plan_without_calling_the_model(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """A dry run that starts the agent would spend money before operator review."""

    def explode(invocation: AgentInvocation) -> AgentProcessResult:
        raise AssertionError("a dry run must never launch the agent")

    write_task_tree(manifest_root, "second-task")
    manifest = _manifest(
        manifest_root,
        tasks=task_table("demo-task", manifest_root)
        + "\n"
        + task_table("second-task", manifest_root),
    )
    output_dir = tmp_path / "out"

    result = run_campaign(
        manifest,
        config_file,
        3,
        output_dir,
        True,
        agent_launcher=explode,
        agent_executable=("fake-agent",),
    )

    assert result.dry_run is True
    assert result.runs == ()
    assert result.plan is not None
    assert result.plan.task_count == 2
    assert result.plan.repeats == 3
    assert result.plan.total_runs == 6
    assert result.plan.max_model_requests == 24
    assert result.plan.workspace_root == str(output_dir / "runs")
    assert result.plan.output_dir == str(output_dir)
    assert output_dir.exists() is False


def test_existing_campaign_directory_is_never_overwritten(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """Overwriting a campaign would destroy the immutable record of an earlier run."""
    manifest = _manifest(manifest_root)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "runs.jsonl").write_text("{}\n", encoding="utf-8")

    with pytest.raises(CampaignError, match="already"):
        run_campaign(
            manifest,
            config_file,
            1,
            output_dir,
            False,
            agent_launcher=RecordingLauncher(mutate=_apply_gold),
            agent_executable=("fake-agent",),
        )


def test_agent_configuration_error_is_recorded_as_a_harness_setup_outcome(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """Harness-generated bad inputs must not be charged to the agent's capability."""
    manifest = _manifest(manifest_root)
    launcher = RecordingLauncher(exit_code=2, write_report=False)

    result = run_campaign(
        manifest,
        config_file,
        1,
        tmp_path / "out",
        False,
        agent_launcher=launcher,
        agent_executable=("fake-agent",),
    )

    run = result.runs[0]
    assert run.outcome == "HARNESS_SETUP"
    assert run.strict_success is False
    assert run.failure_stage == "setup"
    assert run.failure_kind == "harness_setup"


def test_workspace_escape_is_detected_by_the_canary(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """Silent writes outside the workspace would invalidate the whole measurement."""

    def escape(invocation: AgentInvocation) -> None:
        _apply_gold(invocation)
        invocation.canary.write_text("tampered\n", encoding="utf-8")

    manifest = _manifest(manifest_root)

    result = run_campaign(
        manifest,
        config_file,
        1,
        tmp_path / "out",
        False,
        agent_launcher=RecordingLauncher(mutate=escape),
        agent_executable=("fake-agent",),
    )

    run = result.runs[0]
    assert run.detected_workspace_escape is True
    assert run.strict_success is False


def test_forbidden_path_modification_blocks_strict_success(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """A passing oracle must not excuse edits to paths the manifest protects."""

    def touch_forbidden(invocation: AgentInvocation) -> None:
        _apply_gold(invocation)
        (invocation.workspace / "README.md").write_text("rewritten\n", encoding="utf-8")

    manifest = _manifest(manifest_root)

    result = run_campaign(
        manifest,
        config_file,
        1,
        tmp_path / "out",
        False,
        agent_launcher=RecordingLauncher(mutate=touch_forbidden),
        agent_executable=("fake-agent",),
    )

    run = result.runs[0]
    assert run.oracle_passed is True
    assert run.forbidden_changes == ["README.md"]
    assert run.strict_success is False
    assert run.failure_kind == "forbidden_modification"


def test_successful_campaign_records_diff_and_tree_hashes_without_absolute_paths(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """A run record that leaks local paths cannot be published as a public result."""
    manifest = _manifest(manifest_root)
    output_dir = tmp_path / "out"

    result = run_campaign(
        manifest,
        config_file,
        1,
        output_dir,
        False,
        agent_launcher=RecordingLauncher(mutate=_apply_gold),
        agent_executable=("fake-agent",),
    )

    run = result.runs[0]
    document = json.loads(
        (output_dir / "runs" / "demo-task" / "repeat-1" / "run.json").read_text(encoding="utf-8")
    )
    serialized = json.dumps(document)
    assert run.strict_success is True
    assert run.artifact_correct is True
    assert document["schema_version"] == "run-v1"
    assert document["provider"] == "anthropic_messages"
    assert document["hashes"]["baseline_tree"] != document["hashes"]["workspace_tree"]
    assert document["hashes"]["diff"]
    assert document["modifications"]["files_modified"] == 1
    assert str(tmp_path) not in serialized
    assert "Change VALUE" not in serialized


def test_run_document_copies_the_model_identity_from_the_agent_report(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """A published run must name the model that served it, taken from the process that ran."""
    manifest = _manifest(manifest_root)
    output_dir = tmp_path / "out"

    result = run_campaign(
        manifest,
        config_file,
        1,
        output_dir,
        False,
        agent_launcher=RecordingLauncher(mutate=_apply_gold),
        agent_executable=("fake-agent",),
    )

    document = json.loads(
        (output_dir / "runs" / "demo-task" / "repeat-1" / "run.json").read_text(encoding="utf-8")
    )
    assert result.runs[0].model_identity == {
        "name": "claude-stub-model-2026",
        "context_window": 64000,
        "max_output_tokens": 8192,
        "stream": True,
    }
    assert document["model_identity"] == document["agent_report"]["model_identity"]


def test_all_campaign_durations_come_from_the_injected_monotonic_clock(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """Wall-clock timing would make durations vary with clock adjustments."""
    ticks = iter(range(0, 10_000))
    manifest = _manifest(manifest_root)

    result = run_campaign(
        manifest,
        config_file,
        1,
        tmp_path / "out",
        False,
        agent_launcher=RecordingLauncher(mutate=_apply_gold),
        agent_executable=("fake-agent",),
        monotonic=lambda: float(next(ticks)),
    )

    durations = result.runs[0].durations
    assert durations.total_ms > 0
    assert durations.total_ms % 1000 == 0
    assert durations.agent_process_ms == 1000


def test_public_task_set_scores_end_to_end_with_a_stub_agent(
    config_file: Path,
    tmp_path: Path,
) -> None:
    """The delivered task set must be scorable by the real oracles before any live run."""
    manifest = validate_manifest(PUBLIC_MANIFEST / "manifest.toml")

    def apply_gold(invocation: AgentInvocation) -> None:
        task = next(item for item in manifest.tasks if item.task_id == invocation.task_id)
        shutil.copytree(task.gold_overlay, invocation.workspace, dirs_exist_ok=True)

    launcher = RecordingLauncher(mutate=apply_gold)
    output_dir = tmp_path / "campaign"

    result = run_campaign(
        manifest,
        config_file,
        1,
        output_dir,
        False,
        agent_launcher=launcher,
        agent_executable=("fake-agent",),
        agent_commit="0" * 40,
        campaign_id="offline-example",
    )

    summary = result.summary
    assert summary is not None
    assert [item.ok for item in result.setup] == [True, True, True, True]
    assert summary.started_runs == 4
    assert summary.valid_runs == 4
    assert summary.strict_success_runs == 4
    assert summary.task_completion_rate == 1.0
    assert [run.forbidden_changes for run in result.runs] == [[], [], [], []]
    assert all(run.detected_workspace_escape is False for run in result.runs)
    assert (output_dir / "runs.jsonl").is_file()
    assert (output_dir / "reports" / "summary.json").is_file()
    assert (output_dir / "reports" / "summary.csv").is_file()
    assert (output_dir / "reports" / "report.md").is_file()


def _invocation(argv: tuple[str, ...], tmp_path: Path) -> AgentInvocation:
    return AgentInvocation(
        task_id="demo-task",
        repeat=1,
        argv=argv,
        config=tmp_path / "config.toml",
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
        prompt_file=tmp_path / "prompt.md",
        report_out=tmp_path / "agent-report.json",
        command_policy=tmp_path / "command-policy.json",
        canary=tmp_path / "canary.txt",
        timeout_seconds=30,
    )


def test_default_launcher_returns_the_agent_exit_code(tmp_path: Path) -> None:
    """A launcher that swallowed the exit code would hide configuration rejections."""
    invocation = _invocation((sys.executable, "-c", "raise SystemExit(2)"), tmp_path)

    outcome = launch_agent(invocation)

    assert outcome.exit_code == 2
    assert outcome.timed_out is False


def test_default_launcher_reports_a_missing_entry_point(tmp_path: Path) -> None:
    """Silently scoring an unlaunchable agent as a failure would misattribute the cause."""
    invocation = _invocation(("coding-agent-that-does-not-exist",), tmp_path)

    with pytest.raises(CampaignError, match="unable to launch"):
        launch_agent(invocation)
