"""Offline guards for the real-model smoke harness in ``tests/live``.

Two things must hold without a network: the whole live package stays skipped unless it is
explicitly enabled, and every live task tree really behaves the way its scenario claims.
The second half is what makes the live assertions meaningful — a baseline that already
passed, or a gold overlay that did not, would grade the model on nothing.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.live.tasks import LIVE_TASKS, VERIFY_COMMAND, LiveTask, materialize, run_local_check

_REPOSITORY = Path(__file__).resolve().parents[2]
_LIVE_ENVIRONMENT_NAMES = (
    "RUN_LIVE_TESTS",
    "LIVE_MODEL",
    "LIVE_BASE_URL",
    "LIVE_API_KEY_ENV",
    "LIVE_ALT_MODEL",
    "LIVE_ALT_BASE_URL",
    "LIVE_ALT_API_KEY_ENV",
    "LIVE_REPORT_DIR",
)
_SKIPPED = re.compile(r"(\d+) skipped")


def _collect_live_package(*, switch: str | None) -> subprocess.CompletedProcess[str]:
    """Run ``pytest tests/live`` in a child process with a controlled environment."""
    environment = dict(os.environ)
    for name in _LIVE_ENVIRONMENT_NAMES:
        environment.pop(name, None)
    if switch is not None:
        environment["RUN_LIVE_TESTS"] = switch
    return subprocess.run(  # noqa: S603 - fixed interpreter running this project's own tests
        [sys.executable, "-m", "pytest", "tests/live", "-q", "-p", "no:cacheprovider"],
        cwd=str(_REPOSITORY),
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _assert_only_skips(completed: subprocess.CompletedProcess[str]) -> int:
    """Return the skip count after proving pytest's own count line reports nothing else."""
    report = completed.stdout + completed.stderr
    assert completed.returncode == 0, report
    summary = next((line for line in reversed(report.splitlines()) if _SKIPPED.search(line)), None)
    assert summary is not None, report
    for forbidden in ("passed", "failed", "error"):
        assert forbidden not in summary, report
    matched = _SKIPPED.search(summary)
    assert matched is not None
    return int(matched.group(1))


def test_live_package_is_entirely_skipped_without_the_switch() -> None:
    """Without RUN_LIVE_TESTS=1 no live test may execute, whatever else the environment holds."""
    completed = _collect_live_package(switch=None)

    assert _assert_only_skips(completed) >= len(LIVE_TASKS) + 1


def test_live_package_still_skips_when_enabled_without_a_configured_model() -> None:
    """The switch alone must not reach the network: the model must also be named explicitly."""
    completed = _collect_live_package(switch="1")

    assert _assert_only_skips(completed) >= len(LIVE_TASKS) + 1


@pytest.mark.parametrize("task", LIVE_TASKS, ids=lambda task: task.task_id)
def test_live_task_baseline_fails_and_gold_overlay_passes(task: LiveTask, tmp_path: Path) -> None:
    """A live scenario only measures the agent when its baseline and gold outcomes differ."""
    workspace = materialize(task.baseline, tmp_path / "workspace")
    home = tmp_path / "home"

    baseline = {check.name: run_local_check(check, workspace, home=home) for check in task.checks}
    for relative in task.expected_files:
        assert not (workspace / relative).exists()
    materialize(task.gold, workspace)
    gold = {check.name: run_local_check(check, workspace, home=home) for check in task.checks}

    expected_baseline = {
        check.name: 0 if check.must_pass_at_baseline else 1 for check in task.checks
    }
    assert baseline == expected_baseline
    assert gold == dict.fromkeys(gold, 0)
    assert any(not check.must_pass_at_baseline for check in task.checks)
    for relative in task.expected_files:
        assert (workspace / relative).is_file()


@pytest.mark.parametrize("task", LIVE_TASKS, ids=lambda task: task.task_id)
def test_live_task_prompt_and_policy_name_the_same_single_command(task: LiveTask) -> None:
    """A prompt that quotes a command the policy does not allow would test the allowlist."""
    assert task.commands == ((VERIFY_COMMAND, "."),)
    assert VERIFY_COMMAND in task.prompt
    assert task.protected
    for relative in task.protected:
        assert relative in task.baseline
