"""The four real-model scenarios required by section 17.4 of ``doc/项目设计方案.md``.

Each test drives the shipped ``coding-agent run`` entry point against a real
Anthropic-compatible Messages service, then grades the result with checks this harness runs
itself outside the workspace. Nothing here asserts on model wording: the assertions are the
run state, the stop reason, the tool-call statistics the run report already records, the
protected files that must survive, and the exit codes of the local checks.

Every test is skipped unless ``RUN_LIVE_TESTS=1`` and the environment names a model.
"""

from __future__ import annotations

from tests.live import tasks
from tests.live.harness import LIVE_MAX_ROUNDS, LiveModel, LiveRun, LiveRunner
from tests.live.tasks import LiveTask, declared_digests, digests, run_local_check


def _assert_run_completed_cleanly(run: LiveRun) -> None:
    facts = run.facts
    assert facts.exit_code == 0, f"{facts.task_id}: exit {facts.exit_code}: {run.stderr}"
    assert facts.state == "COMPLETED"
    assert facts.stop_reason == "COMPLETED"
    assert facts.error_kind is None
    assert facts.succeeded is True
    assert 1 <= facts.rounds <= LIVE_MAX_ROUNDS
    assert facts.attempts >= facts.rounds
    assert facts.usage_coverage is not None


def _assert_protected_files_survived(task: LiveTask, run: LiveRun) -> None:
    expected = declared_digests(task.baseline, task.protected)
    assert digests(task.protected, run.workspace) == expected


def _assert_expected_files_exist(task: LiveTask, run: LiveRun) -> None:
    for relative in task.expected_files:
        target = run.workspace / relative
        assert target.is_file(), f"{task.task_id}: {relative} was not created"
        assert target.stat().st_size > 0


def _assert_local_checks_pass(task: LiveTask, run: LiveRun) -> None:
    home = run.run_dir / "check-home"
    outcomes = {
        check.name: run_local_check(check, run.workspace, home=home) for check in task.checks
    }
    assert outcomes == dict.fromkeys(outcomes, 0), f"{task.task_id}: local checks {outcomes}"


def _grade(task: LiveTask, run: LiveRun) -> None:
    _assert_run_completed_cleanly(run)
    _assert_protected_files_survived(task, run)
    _assert_expected_files_exist(task, run)
    _assert_local_checks_pass(task, run)


def test_creates_a_small_file_and_runs_the_project_test(
    live_runner: LiveRunner,
    primary_model: LiveModel,
) -> None:
    """Scenario 1: the agent must author a new file and prove it with the allowed command."""
    task = tasks.CREATE_FILE_AND_RUN_TEST

    run = live_runner.run(task, primary_model)

    _grade(task, run)
    assert run.facts.tool_counts("write_file")["succeeded"] >= 1
    assert run.facts.tool_counts("run_command")["succeeded"] >= 1


def test_modifies_an_existing_function_without_breaking_its_tests(
    live_runner: LiveRunner,
    primary_model: LiveModel,
) -> None:
    """Scenario 2: a local edit must add the new behaviour and keep the original suite green."""
    task = tasks.MODIFY_FUNCTION_KEEP_REGRESSION

    run = live_runner.run(task, primary_model)

    _grade(task, run)
    assert run.facts.tool_counts("write_file")["succeeded"] >= 1
    changed = (run.workspace / "mathkit" / "ranges.py").read_text(encoding="utf-8")
    assert changed != task.baseline["mathkit/ranges.py"]


def test_self_corrects_after_a_failing_tool_result(
    live_runner: LiveRunner,
    primary_model: LiveModel,
) -> None:
    """Scenario 3: a failing command must be read as a result and recovered from, not fatal."""
    task = tasks.SELF_CORRECT_AFTER_TOOL_FAILURE

    run = live_runner.run(task, primary_model)

    _grade(task, run)
    commands = run.facts.tool_counts("run_command")
    assert commands["failed"] >= 1, "the baseline command was expected to fail at least once"
    assert commands["succeeded"] >= 1, "the agent never reached a passing command"


def test_completes_the_same_task_through_a_second_messages_configuration(
    live_runner: LiveRunner,
    alternate_model: LiveModel,
) -> None:
    """Scenario 4: swapping the configured service or model must need no code change."""
    task = tasks.CREATE_FILE_AND_RUN_TEST

    run = live_runner.run(task, alternate_model)

    _grade(task, run)
    assert run.facts.model_name == alternate_model.model
    assert run.facts.tool_counts("run_command")["succeeded"] >= 1
