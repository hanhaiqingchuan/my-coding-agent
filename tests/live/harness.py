"""The live-run harness: environment-only credentials, real CLI entry point, redacted records.

This module holds everything the live tests need except the pytest fixtures, so a test never
has to import a ``conftest``. Runs go through the shipped ``coding-agent run`` entry point as
a subprocess, exactly like the evaluation harness, so the smoke exercises the delivered path
rather than an in-process shortcut.

Nothing here ever writes a credential anywhere: the model configuration carries only the
*name* of the environment variable, and every string this module prints or persists is passed
through :func:`redact` first.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from coding_agent.evaluation.runner import resolve_agent_executable
from tests.live.tasks import LiveTask, materialize

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_TIMEOUT_SECONDS = 300
LIVE_MAX_ROUNDS = 12
_REDACTED = "[redacted]"


@dataclass(frozen=True, slots=True)
class LiveModel:
    """One Anthropic-compatible Messages configuration, credential deliberately excluded."""

    label: str
    base_url: str
    model: str
    api_key_env: str

    def config_toml(self) -> str:
        return (
            "[model]\n"
            f'base_url = "{self.base_url}"\n'
            f'model = "{self.model}"\n'
            f'api_key_env = "{self.api_key_env}"\n'
            "stream = true\n\n"
            "[agent]\n"
            f"max_rounds = {LIVE_MAX_ROUNDS}\n"
        )


@dataclass(frozen=True, slots=True)
class LiveFacts:
    """The section 17.4 record for one real run: no wording, no path, no credential."""

    task_id: str
    model_label: str
    succeeded: bool
    exit_code: int
    state: str
    stop_reason: str | None
    error_kind: str | None
    model_name: str | None
    rounds: int
    attempts: int
    network_retries: int
    usage: Mapping[str, int | None]
    usage_coverage: float | None
    tools: Mapping[str, object]
    compactions: int
    compaction_requests: int

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "model_label": self.model_label,
            "succeeded": self.succeeded,
            "exit_code": self.exit_code,
            "state": self.state,
            "stop_reason": self.stop_reason,
            "error_kind": self.error_kind,
            "model_name": self.model_name,
            "rounds": self.rounds,
            "attempts": self.attempts,
            "network_retries": self.network_retries,
            "usage": dict(self.usage),
            "usage_coverage": self.usage_coverage,
            "tools": dict(self.tools),
            "compactions": self.compactions,
            "compaction_requests": self.compaction_requests,
        }

    def tool_counts(self, name: str) -> dict[str, int]:
        """Per-tool proposed/succeeded/failed counts, zero-filled for an unused tool."""
        by_name = self.tools.get("by_name")
        counts = by_name.get(name) if isinstance(by_name, Mapping) else None
        if isinstance(counts, Mapping):
            return {key: int(value) for key, value in counts.items()}
        return {"proposed": 0, "succeeded": 0, "failed": 0}

    def summary_line(self) -> str:
        """One grep-able line carrying every fact section 17.4 asks a live run to record."""
        tools = self.tools
        return (
            f"LIVE_FACTS task={self.task_id} model_label={self.model_label} "
            f"succeeded={self.succeeded} exit_code={self.exit_code} state={self.state} "
            f"stop_reason={self.stop_reason} error_kind={self.error_kind} "
            f"rounds={self.rounds} attempts={self.attempts} "
            f"network_retries={self.network_retries} "
            f"tool_calls_proposed={tools.get('proposed')} "
            f"tool_calls_executed={tools.get('executed')} "
            f"tool_calls_failed={tools.get('failed')} "
            f"usage={json.dumps(dict(self.usage), sort_keys=True)} "
            f"usage_coverage={self.usage_coverage} "
            f"compactions={self.compactions} "
            f"compaction_requests={self.compaction_requests}"
        )


@dataclass(frozen=True, slots=True)
class LiveRun:
    """One finished live run: the facts plus the workspace the harness may inspect."""

    facts: LiveFacts
    workspace: Path
    run_dir: Path
    stderr: str


def redact(text: str, secrets: Sequence[str]) -> str:
    """Replace every credential value with a fixed placeholder."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, _REDACTED)
    return text


def model_from_environment(
    label: str,
    *,
    model_variable: str,
    base_url_variable: str,
    key_env_variable: str,
    default_base_url: str,
    default_key_env: str,
) -> LiveModel:
    """Build one configuration from the environment, skipping the test when it is absent."""
    model = os.environ.get(model_variable, "").strip()
    if not model:
        pytest.skip(f"set {model_variable} to the {label} Anthropic-compatible model name")
    base_url = os.environ.get(base_url_variable, "").strip() or default_base_url
    key_env = os.environ.get(key_env_variable, "").strip() or default_key_env
    if not os.environ.get(key_env, "").strip():
        pytest.skip(f"set {key_env} to the {label} model credential")
    return LiveModel(label=label, base_url=base_url, model=model, api_key_env=key_env)


def timeout_from_environment() -> int:
    raw = os.environ.get("LIVE_TIMEOUT_SECONDS", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_TIMEOUT_SECONDS


class LiveRunner:
    """Drive one task through the shipped headless CLI and record the redacted facts."""

    def __init__(self, root: Path, timeout_seconds: int) -> None:
        self._root = root
        self._timeout_seconds = timeout_seconds
        self._recorded: list[LiveFacts] = []

    @property
    def recorded(self) -> tuple[LiveFacts, ...]:
        return tuple(self._recorded)

    def run(self, task: LiveTask, model: LiveModel) -> LiveRun:
        """Materialize the task, run the agent once, and return the graded facts."""
        run_dir = self._root / f"{task.task_id}--{model.label}"
        if run_dir.exists():
            raise AssertionError(f"live run directory already exists: {run_dir.name}")
        workspace = materialize(task.baseline, run_dir / "workspace")
        (run_dir / "data").mkdir(parents=True)
        prompt_file = run_dir / "prompt.md"
        prompt_file.write_text(task.prompt, encoding="utf-8")
        config_file = run_dir / "config.toml"
        config_file.write_text(model.config_toml(), encoding="utf-8")
        policy_file = run_dir / "command-policy.json"
        policy_file.write_text(_policy_document(task), encoding="utf-8")
        report_file = run_dir / "agent-report.json"

        argv = [
            *resolve_agent_executable(),
            "run",
            "--config",
            str(config_file),
            "--workspace",
            str(workspace),
            "--data-dir",
            str(run_dir / "data"),
            "--prompt-file",
            str(prompt_file),
            "--report-out",
            str(report_file),
            "--yes",
            "--ack-unsafe-auto-approve",
            "--command-policy",
            str(policy_file),
        ]
        secrets = environment_secrets(model)
        try:
            completed = subprocess.run(  # noqa: S603 - fixed public CLI argv
                argv,
                cwd=str(run_dir),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as expired:
            raise AssertionError(
                f"{task.task_id}: the agent exceeded {self._timeout_seconds}s"
            ) from expired
        stderr = redact(completed.stderr, secrets)
        if not report_file.is_file():
            raise AssertionError(
                f"{task.task_id}: exit {completed.returncode} without a run report: {stderr}"
            )
        report_text = report_file.read_text(encoding="utf-8")
        for secret in secrets:
            assert secret not in report_text, f"{task.task_id}: the run report leaked a credential"
        facts = build_facts(task, model, completed.returncode, json.loads(report_text))
        self._record(run_dir, facts, secrets)
        return LiveRun(facts=facts, workspace=workspace, run_dir=run_dir, stderr=stderr)

    def _record(self, run_dir: Path, facts: LiveFacts, secrets: Sequence[str]) -> None:
        """Persist and print the section 17.4 record, redacted before it leaves memory."""
        self._recorded.append(facts)
        document = json.dumps(facts.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        local_copy = run_dir / "live-facts.json"
        local_copy.write_text(redact(document, secrets) + "\n", encoding="utf-8")
        print(redact(facts.summary_line(), secrets))
        export_directory = os.environ.get("LIVE_REPORT_DIR", "").strip()
        if export_directory:
            destination = Path(export_directory).expanduser()
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local_copy, destination / f"{run_dir.name}.json")


def environment_secrets(model: LiveModel) -> tuple[str, ...]:
    """Every credential value in play, so nothing this harness emits can contain one."""
    names = {model.api_key_env, DEFAULT_API_KEY_ENV}
    for variable in ("LIVE_API_KEY_ENV", "LIVE_ALT_API_KEY_ENV"):
        configured = os.environ.get(variable, "").strip()
        if configured:
            names.add(configured)
    return tuple(sorted({value for name in names if (value := os.environ.get(name, ""))}))


def build_facts(
    task: LiveTask,
    model: LiveModel,
    exit_code: int,
    report: Mapping[str, object],
) -> LiveFacts:
    """Project one ``run-report-v1`` document into the live record."""
    main = _section(_section(report, "model"), "main")
    compaction = _section(report, "compaction")
    identity = _section(report, "model_identity")
    usage = _section(main, "usage")
    coverage = main.get("usage_coverage")
    name = identity.get("name")
    return LiveFacts(
        task_id=task.task_id,
        model_label=model.label,
        succeeded=exit_code == 0 and report.get("state") == "COMPLETED",
        exit_code=exit_code,
        state=str(report.get("state")),
        stop_reason=_optional_text(report.get("stop_reason")),
        error_kind=_optional_text(report.get("error_kind")),
        model_name=name if isinstance(name, str) else None,
        rounds=int(main.get("requests") or 0),
        attempts=int(main.get("attempts") or 0),
        network_retries=int(main.get("network_retries") or 0),
        usage={key: _optional_int(value) for key, value in usage.items()},
        usage_coverage=float(coverage) if isinstance(coverage, int | float) else None,
        tools=_section(report, "tools"),
        compactions=int(compaction.get("count") or 0),
        compaction_requests=int(compaction.get("requests") or 0),
    )


def _policy_document(task: LiveTask) -> str:
    return json.dumps(
        {
            "schema_version": "command-policy-v1",
            "allowed": [{"command": command, "cwd": cwd} for command, cwd in task.commands],
        },
        indent=2,
    )


def _section(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    nested = value.get(key)
    return nested if isinstance(nested, Mapping) else {}


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = [
    "DEFAULT_API_KEY_ENV",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "LIVE_MAX_ROUNDS",
    "LiveFacts",
    "LiveModel",
    "LiveRun",
    "LiveRunner",
    "build_facts",
    "environment_secrets",
    "model_from_environment",
    "redact",
    "timeout_from_environment",
]
