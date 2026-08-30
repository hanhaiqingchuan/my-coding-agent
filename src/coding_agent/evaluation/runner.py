"""Campaign orchestration for the evaluation harness.

The runner drives the shipped ``coding-agent run`` entry point as a subprocess and
never imports the Agent Loop, the Run Coordinator, or any tool internals. Every
repeat gets a fresh workspace copied from the read-only baseline plus its own data
directory, and oracles always execute outside that workspace.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from coding_agent.config import (
    AppSettings,
    ConfigurationError,
    load_settings,
    resolve_api_key,
)
from coding_agent.evaluation.judge import (
    build_transcript_excerpt,
    judge_run,
    write_judgement,
)
from coding_agent.evaluation.manifest import (
    IGNORED_NAMES,
    EvaluationManifest,
    TaskSpec,
    content_hash,
    tree_files,
    tree_hash,
    workspace_scope,
)
from coding_agent.evaluation.report import (
    AGENT_ARGV_OPTIONS,
    OUTCOME_HARNESS_ORACLE_ERROR,
    OUTCOME_HARNESS_SETUP,
    CompactionFacts,
    ModelFacts,
    ModificationFacts,
    OracleFacts,
    RunResult,
    Summary,
    ToolFacts,
    UsageFacts,
    run_document,
    score_result,
    summarize,
    write_run_document,
)
from coding_agent.model.anthropic_messages import AnthropicMessagesModel
from coding_agent.model.protocol import ModelGateway
from coding_agent.runtime.metrics import canonical_hash

CANARY_TEXT = "evaluation canary; a change here means the agent wrote outside its workspace\n"
_ORACLE_TIMEOUT_SECONDS = 120
_LOCALE_ENV_NAMES = ("LANG", "LC_ALL", "LC_CTYPE")

Monotonic = Callable[[], float]
JudgeHook = Callable[[RunResult, Path, str], Mapping[str, object] | None]
"""Scores one finished run and returns the judgement record it wrote, or None."""


class CampaignError(RuntimeError):
    """Raised when a campaign cannot start or would overwrite an existing record."""


@dataclass(frozen=True, slots=True)
class OracleRun:
    """One deterministic oracle execution outside the agent workspace."""

    exit_code: int
    passed: bool
    errored: bool
    duration_ms: int

    def facts(self) -> OracleFacts:
        return OracleFacts(
            passed=None if self.errored else self.passed,
            exit_code=self.exit_code,
            duration_ms=self.duration_ms,
            errored=self.errored,
        )


@dataclass(frozen=True, slots=True)
class SetupVerification:
    """Proof that a task can distinguish an unfinished baseline from a correct change."""

    task_id: str
    baseline_failed: bool
    baseline_regression_passed: bool
    gold_passed: bool
    gold_regression_passed: bool
    error_variant_failed: bool | None
    oracle_errored: bool
    ok: bool
    detail: str | None


@dataclass(frozen=True, slots=True)
class AgentInvocation:
    """Everything one headless agent process receives, plus the canary that guards it."""

    task_id: str
    repeat: int
    argv: tuple[str, ...]
    config: Path
    workspace: Path
    data_dir: Path
    prompt_file: Path
    report_out: Path
    command_policy: Path
    canary: Path
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class AgentProcessResult:
    exit_code: int
    timed_out: bool


AgentLauncher = Callable[[AgentInvocation], AgentProcessResult]


@dataclass(frozen=True, slots=True)
class CampaignPlan:
    """What a real campaign would do, shown before any model call happens."""

    task_count: int
    repeats: int
    total_runs: int
    max_model_requests: int
    workspace_root: str
    output_dir: str

    def lines(self) -> tuple[str, ...]:
        return (
            f"tasks: {self.task_count}",
            f"repeats per task: {self.repeats}",
            f"total runs: {self.total_runs}",
            f"maximum main model requests: {self.max_model_requests}",
            f"fresh workspaces under: {self.workspace_root}",
            f"results under: {self.output_dir}",
        )


@dataclass(frozen=True, slots=True)
class CampaignResult:
    campaign_id: str
    dry_run: bool
    output_dir: Path | None
    plan: CampaignPlan | None
    runs: tuple[RunResult, ...] = ()
    summary: Summary | None = None
    setup: tuple[SetupVerification, ...] = ()


@dataclass(slots=True)
class _DiffSummary:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    lines_added: int = 0
    lines_removed: int = 0

    @property
    def changed(self) -> list[str]:
        return sorted({*self.added, *self.modified, *self.deleted})


def resolve_agent_executable() -> tuple[str, ...]:
    """Return the shipped console script when installed, otherwise its module entry."""
    console_script = Path(sys.executable).parent / "coding-agent"
    if console_script.is_file():
        return (str(console_script),)
    return (sys.executable, "-m", "coding_agent.cli")


def run_oracle(
    entry: Path,
    workspace: Path,
    *,
    cwd: Path,
    timeout_seconds: int = _ORACLE_TIMEOUT_SECONDS,
    monotonic: Monotonic = time.monotonic,
) -> OracleRun:
    """Run one oracle script outside ``workspace`` and map its exit code to an outcome."""
    # The subprocess gets its own cwd, so every path handed to it must be
    # absolute — a relative workspace would resolve against the oracle's cwd
    # inside the script and silently point at a nonexistent tree.
    entry = entry.resolve()
    workspace = workspace.resolve()
    cwd = cwd.resolve()
    if cwd == workspace or workspace in cwd.parents:
        raise CampaignError("oracle: must never run inside the agent workspace")
    cwd.mkdir(parents=True, exist_ok=True)
    started = monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and manifest-declared entry
            [sys.executable, "-B", str(entry), str(workspace)],
            cwd=str(cwd),
            env=_oracle_environment(cwd),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return OracleRun(
            exit_code=-1,
            passed=False,
            errored=True,
            duration_ms=_elapsed_ms(started, monotonic()),
        )
    except OSError:
        return OracleRun(
            exit_code=-1,
            passed=False,
            errored=True,
            duration_ms=_elapsed_ms(started, monotonic()),
        )
    code = completed.returncode
    return OracleRun(
        exit_code=code,
        passed=code == 0,
        errored=code not in {0, 1},
        duration_ms=_elapsed_ms(started, monotonic()),
    )


def verify_task_setup(
    task: TaskSpec,
    *,
    scratch: Path,
    monotonic: Monotonic = time.monotonic,
) -> SetupVerification:
    """Prove the baseline fails, the gold overlay passes, and the error variant fails."""
    baseline_workspace = _materialize(task.baseline, scratch / "baseline" / "workspace")
    baseline_target = run_oracle(
        task.target_oracle,
        baseline_workspace,
        cwd=scratch / "baseline" / "oracle",
        monotonic=monotonic,
    )
    baseline_regression = run_oracle(
        task.regression_oracle,
        baseline_workspace,
        cwd=scratch / "baseline" / "oracle",
        monotonic=monotonic,
    )
    gold_workspace = _materialize(task.baseline, scratch / "gold" / "workspace")
    _overlay(task.gold_overlay, gold_workspace)
    gold_target = run_oracle(
        task.target_oracle,
        gold_workspace,
        cwd=scratch / "gold" / "oracle",
        monotonic=monotonic,
    )
    gold_regression = run_oracle(
        task.regression_oracle,
        gold_workspace,
        cwd=scratch / "gold" / "oracle",
        monotonic=monotonic,
    )
    error_target: OracleRun | None = None
    if task.error_overlay is not None:
        error_workspace = _materialize(task.baseline, scratch / "error" / "workspace")
        _overlay(task.error_overlay, error_workspace)
        error_target = run_oracle(
            task.target_oracle,
            error_workspace,
            cwd=scratch / "error" / "oracle",
            monotonic=monotonic,
        )

    runs = [baseline_target, baseline_regression, gold_target, gold_regression]
    if error_target is not None:
        runs.append(error_target)
    errored = any(item.errored for item in runs)
    baseline_failed = baseline_target.passed is False and not baseline_target.errored
    baseline_regression_passed = baseline_regression.passed and not baseline_regression.errored
    gold_passed = gold_target.passed and not gold_target.errored
    gold_regression_passed = gold_regression.passed and not gold_regression.errored
    error_variant_failed = (
        None
        if error_target is None
        else (error_target.passed is False and not error_target.errored)
    )
    detail = _setup_detail(
        errored=errored,
        baseline_failed=baseline_failed,
        baseline_regression_passed=baseline_regression_passed,
        gold_passed=gold_passed,
        gold_regression_passed=gold_regression_passed,
        error_variant_failed=error_variant_failed,
    )
    return SetupVerification(
        task_id=task.task_id,
        baseline_failed=baseline_failed,
        baseline_regression_passed=bool(baseline_regression_passed),
        gold_passed=bool(gold_passed),
        gold_regression_passed=bool(gold_regression_passed),
        error_variant_failed=error_variant_failed,
        oracle_errored=errored,
        ok=detail is None,
        detail=detail,
    )


def build_judge_hook(
    config: Path,
    *,
    gateway: ModelGateway | None = None,
    environ: Mapping[str, str] | None = None,
) -> JudgeHook:
    """Build the campaign's judge hook from the same configuration the agent uses.

    The judge reuses the shipped Anthropic Messages adapter with the campaign's own
    ``ModelSettings``, so a judged campaign needs no second model configuration; its
    one model request retries through the same ``RetryingInvoker`` policy the agent
    uses (``settings.retry``), so a transient transport failure or rate limit backs
    off instead of recording a ``judge_error``. The returned hook never raises: a
    judge that still cannot answer after its retry budget records a ``judge_error``
    instead of aborting the campaign.
    """
    try:
        settings = load_settings(config, {}, {})
        api_key = resolve_api_key(settings, environ if environ is not None else os.environ)
    except ConfigurationError as error:
        raise CampaignError(f"judge: {error}") from error
    model = gateway if gateway is not None else AnthropicMessagesModel(settings.model, api_key)

    def hook(result: RunResult, run_dir: Path, campaign_id: str) -> Mapping[str, object] | None:
        document = run_document(result, campaign_id=campaign_id)
        excerpt = build_transcript_excerpt(document)
        judgement = asyncio.run(
            judge_run(
                document,
                excerpt,
                settings.model,
                gateway=model,
                retry=settings.retry,
                max_output_tokens=settings.evaluation.judge_max_output_tokens,
            )
        )
        write_judgement(
            run_dir / "judgement.json",
            judgement,
            campaign_id=campaign_id,
            task_id=result.task_id,
            repeat=result.repeat,
        )
        return judgement.to_document(
            campaign_id=campaign_id, task_id=result.task_id, repeat=result.repeat
        )

    return hook


def run_campaign(
    manifest: EvaluationManifest,
    config: Path,
    repeats: int,
    output_dir: Path,
    dry_run: bool,
    *,
    agent_launcher: AgentLauncher | None = None,
    agent_executable: Sequence[str] | None = None,
    monotonic: Monotonic = time.monotonic,
    agent_commit: str | None = None,
    campaign_id: str | None = None,
    judge: JudgeHook | None = None,
) -> CampaignResult:
    """Run every task and repeat serially through the public headless CLI.

    A campaign writes only the records it owns: one immutable ``run-v1`` document per repeat
    plus ``runs.jsonl`` — and, when a judge hook is given, one ``judgement-v1`` record per
    repeat. The derived aggregates belong to ``summarize``, which refuses to overwrite an
    existing artifact, so pre-writing them here would make the documented aggregation
    command impossible to run.
    """
    if repeats < 1:
        raise CampaignError("repeats: must be at least 1")
    settings = _settings(config, dry_run=dry_run)
    plan = CampaignPlan(
        task_count=len(manifest.tasks),
        repeats=repeats,
        total_runs=len(manifest.tasks) * repeats,
        max_model_requests=len(manifest.tasks) * repeats * settings.agent.max_rounds,
        workspace_root=str(output_dir / "runs"),
        output_dir=str(output_dir),
    )
    identifier = campaign_id or f"campaign-{uuid4().hex[:12]}"
    if dry_run:
        return CampaignResult(
            campaign_id=identifier,
            dry_run=True,
            output_dir=None,
            plan=plan,
        )

    if output_dir.exists() and any(output_dir.iterdir()):
        raise CampaignError(f"output: campaign directory already exists: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    executable = tuple(agent_executable) if agent_executable else resolve_agent_executable()
    launcher = agent_launcher or launch_agent
    commit = agent_commit if agent_commit is not None else _agent_commit()
    config_hash = canonical_hash(asdict(settings))

    runs: list[RunResult] = []
    judgements: list[Mapping[str, object]] = []
    verifications: list[SetupVerification] = []
    for task in manifest.tasks:
        verification = verify_task_setup(
            task,
            scratch=output_dir / "setup" / task.task_id,
            monotonic=monotonic,
        )
        verifications.append(verification)
        for repeat in range(1, repeats + 1):
            run_dir = output_dir / "runs" / task.task_id / f"repeat-{repeat}"
            runs.append(
                _run_once(
                    task=task,
                    repeat=repeat,
                    verification=verification,
                    run_dir=run_dir,
                    config=config,
                    config_hash=config_hash,
                    commit=commit,
                    campaign_id=identifier,
                    executable=executable,
                    launcher=launcher,
                    monotonic=monotonic,
                )
            )
            if judge is not None:
                record = judge(runs[-1], run_dir, identifier)
                if record is not None:
                    judgements.append(record)

    _write_jsonl(output_dir / "runs.jsonl", runs, identifier)
    summary = summarize(
        runs,
        campaign_id=identifier,
        agent_commit=commit,
        judgements=judgements,
    )
    return CampaignResult(
        campaign_id=identifier,
        dry_run=False,
        output_dir=output_dir,
        plan=plan,
        runs=tuple(runs),
        summary=summary,
        setup=tuple(verifications),
    )


def _run_once(
    *,
    task: TaskSpec,
    repeat: int,
    verification: SetupVerification,
    run_dir: Path,
    config: Path,
    config_hash: str,
    commit: str | None,
    campaign_id: str,
    executable: tuple[str, ...],
    launcher: AgentLauncher,
    monotonic: Monotonic,
) -> RunResult:
    if run_dir.exists():
        raise CampaignError(f"output: run directory already exists: {run_dir}")
    started = monotonic()
    started_at = datetime.now(UTC).isoformat()
    workspace = _materialize(task.baseline, run_dir / "workspace")
    prompt_file = run_dir / "prompt.md"
    prompt_bytes = task.prompt.read_bytes()
    prompt_file.write_bytes(prompt_bytes)
    policy_file = run_dir / "command-policy.json"
    policy_file.write_text(_policy_document(task), encoding="utf-8")
    canary = run_dir / "canary.txt"
    canary.write_text(CANARY_TEXT, encoding="utf-8")
    baseline_files = tree_files(task.baseline)
    prepared = monotonic()

    result = RunResult(task_id=task.task_id, category=task.category, repeat=repeat)
    result.agent_commit = commit
    result.started_at = started_at
    result.hashes = {
        "config": config_hash,
        "task": _task_hash(task, prompt_bytes),
        "prompt": content_hash(prompt_bytes),
        "tool_schema": None,
        "baseline_tree": tree_hash(baseline_files),
        "workspace_tree": None,
        "diff": None,
    }

    if not verification.ok:
        result.outcome = OUTCOME_HARNESS_SETUP
        result.harness_detail = verification.detail
        return _finalize(result, run_dir, campaign_id, started, prepared, prepared, monotonic)

    invocation = AgentInvocation(
        task_id=task.task_id,
        repeat=repeat,
        argv=(
            *executable,
            "run",
            "--config",
            str(config),
            "--workspace",
            str(workspace),
            "--data-dir",
            str(run_dir / "data"),
            "--prompt-file",
            str(prompt_file),
            "--report-out",
            str(run_dir / "agent-report.json"),
            "--yes",
            "--ack-unsafe-auto-approve",
            "--command-policy",
            str(policy_file),
        ),
        config=config,
        workspace=workspace,
        data_dir=run_dir / "data",
        prompt_file=prompt_file,
        report_out=run_dir / "agent-report.json",
        command_policy=policy_file,
        canary=canary,
        timeout_seconds=task.timeout_seconds,
    )
    process = launcher(invocation)
    finished_agent = monotonic()
    result.agent_exit_code = process.exit_code
    result.agent_timed_out = process.timed_out

    workspace_files = tree_files(workspace)
    diff = _diff(baseline_files, workspace_files, task.baseline, workspace)
    result.hashes["workspace_tree"] = tree_hash(workspace_files)
    result.hashes["diff"] = _diff_hash(baseline_files, workspace_files)
    result.modifications = ModificationFacts(
        files_added=len(diff.added),
        files_modified=len(diff.modified),
        files_deleted=len(diff.deleted),
        lines_added=diff.lines_added,
        lines_removed=diff.lines_removed,
        out_of_scope_paths=[
            path for path in diff.changed if not workspace_scope(task.allowed_paths, path)
        ],
    )
    result.forbidden_changes = [
        path for path in diff.changed if workspace_scope(task.forbidden_paths, path)
    ]
    result.detected_workspace_escape = (
        canary.read_text(encoding="utf-8") != CANARY_TEXT
        or tree_hash(tree_files(task.baseline)) != result.hashes["baseline_tree"]
    )

    report = _read_agent_report(invocation.report_out)
    if report is None:
        if process.exit_code == 2:
            result.outcome = OUTCOME_HARNESS_SETUP
            result.harness_detail = "the agent rejected the harness configuration"
        else:
            result.harness_detail = "the agent produced no run report"
        return _finalize(result, run_dir, campaign_id, started, prepared, finished_agent, monotonic)
    _absorb_agent_report(result, report)

    target = run_oracle(
        task.target_oracle,
        workspace,
        cwd=run_dir / "oracle",
        monotonic=monotonic,
    )
    regression = run_oracle(
        task.regression_oracle,
        workspace,
        cwd=run_dir / "oracle",
        monotonic=monotonic,
    )
    finished_oracle = monotonic()
    result.target_oracle = target.facts()
    result.regression_oracle = regression.facts()
    result.oracle_passed = target.passed and not target.errored
    result.regressions_passed = regression.passed and not regression.errored
    if target.errored or regression.errored:
        result.outcome = OUTCOME_HARNESS_ORACLE_ERROR
        result.harness_detail = "an oracle exited with a reserved code"
    return _finalize(
        result, run_dir, campaign_id, started, prepared, finished_agent, monotonic, finished_oracle
    )


def _finalize(
    result: RunResult,
    run_dir: Path,
    campaign_id: str,
    started: float,
    prepared: float,
    finished_agent: float,
    monotonic: Monotonic,
    finished_oracle: float | None = None,
) -> RunResult:
    end = finished_oracle if finished_oracle is not None else monotonic()
    result.durations.workspace_prepare_ms = _elapsed_ms(started, prepared)
    result.durations.agent_process_ms = _elapsed_ms(prepared, finished_agent)
    result.durations.oracle_ms = _elapsed_ms(finished_agent, end)
    result.durations.total_ms = _elapsed_ms(started, end)
    result.finished_at = datetime.now(UTC).isoformat()
    score_result(result)
    write_run_document(run_dir / "run.json", result, campaign_id)
    return result


def _absorb_agent_report(result: RunResult, report: Mapping[str, object]) -> None:
    result.agent_report = report
    result.state = _text(report.get("state"))
    result.stop_reason = _text(report.get("stop_reason"))
    result.error_kind = _text(report.get("error_kind"))
    result.hashes["tool_schema"] = _text(report.get("tool_schema_hash"))
    result.model_identity = _mapping(report.get("model_identity")) or None
    model = _mapping(report.get("model"))
    main = _mapping(model.get("main"))
    compaction = _mapping(model.get("compaction"))
    usage = _mapping(main.get("usage"))
    result.model = ModelFacts(
        usage=UsageFacts(
            input_tokens=_count(usage.get("input_tokens")),
            output_tokens=_count(usage.get("output_tokens")),
            cache_creation_input_tokens=_count(usage.get("cache_creation_input_tokens")),
            cache_read_input_tokens=_count(usage.get("cache_read_input_tokens")),
        ),
        main_requests=_count(main.get("requests")) or 0,
        compaction_requests=_count(compaction.get("requests")) or 0,
        attempts=(_count(main.get("attempts")) or 0) + (_count(compaction.get("attempts")) or 0),
        network_retries=(_count(main.get("network_retries")) or 0)
        + (_count(compaction.get("network_retries")) or 0),
        usage_coverage=_ratio(main.get("usage_coverage")),
    )
    tools = _mapping(report.get("tools"))
    result.tools = ToolFacts(
        proposed=_count(tools.get("proposed")) or 0,
        executed=_count(tools.get("executed")) or 0,
        succeeded=_count(tools.get("succeeded")) or 0,
        failed=_count(tools.get("failed")) or 0,
        skipped=_count(tools.get("skipped")) or 0,
        duplicate_calls=_count(tools.get("duplicate_calls")) or 0,
        output_bytes=_count(tools.get("output_bytes")) or 0,
        truncated=_count(tools.get("truncated")) or 0,
    )
    facts = _mapping(report.get("compaction"))
    error = _mapping(facts.get("estimate_error"))
    result.compaction = CompactionFacts(
        count=_count(facts.get("count")) or 0,
        requests=_count(facts.get("requests")) or 0,
        above_target=bool(facts.get("above_target")),
        input_tokens_before=_count(facts.get("input_tokens_before")),
        input_tokens_after=_count(facts.get("input_tokens_after")),
        estimated_summary_tokens=_count(error.get("estimated_summary_tokens")),
        provider_summary_output_tokens=_count(error.get("provider_summary_output_tokens")),
        estimated_minus_provider_tokens=_count(error.get("estimated_minus_provider_tokens")),
    )
    durations = _mapping(report.get("durations"))
    result.durations.agent_monotonic_ms = _count(durations.get("agent_monotonic_ms"))
    result.durations.retry_wait_monotonic_ms = _count(durations.get("retry_wait_monotonic_ms"))
    result.durations.tool_execution_monotonic_ms = _count(
        durations.get("tool_execution_monotonic_ms")
    )


def launch_agent(invocation: AgentInvocation) -> AgentProcessResult:
    """Run one headless agent process, treating an exhausted task timeout as a timeout."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed public CLI argv
            list(invocation.argv),
            stdin=subprocess.DEVNULL,
            timeout=invocation.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return AgentProcessResult(exit_code=-1, timed_out=True)
    except OSError as error:
        raise CampaignError(f"agent: unable to launch {invocation.argv[0]}") from error
    return AgentProcessResult(exit_code=completed.returncode, timed_out=False)


def _settings(config: Path, *, dry_run: bool) -> AppSettings:
    if not config.is_file():
        if not dry_run:
            raise CampaignError(f"config: file not found: {config}")
        return load_settings(None, {}, {})
    try:
        return load_settings(config, {}, {})
    except ConfigurationError as error:
        raise CampaignError(f"config: {error}") from error


def _policy_document(task: TaskSpec) -> str:
    document = {
        "schema_version": "command-policy-v1",
        "allowed": [{"command": entry["command"], "cwd": entry["cwd"]} for entry in task.commands],
    }
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _task_hash(task: TaskSpec, prompt_bytes: bytes) -> str:
    return canonical_hash(
        {
            "task_id": task.task_id,
            "category": task.category,
            "allowed_paths": list(task.allowed_paths),
            "forbidden_paths": list(task.forbidden_paths),
            "timeout_seconds": task.timeout_seconds,
            "commands": [dict(entry) for entry in task.commands],
            "prompt": content_hash(prompt_bytes),
            "baseline_tree": tree_hash(tree_files(task.baseline)),
            "gold_tree": tree_hash(tree_files(task.gold_overlay)),
            "target_oracle": content_hash(task.target_oracle.read_bytes()),
            "regression_oracle": content_hash(task.regression_oracle.read_bytes()),
        }
    )


def _materialize(baseline: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        baseline,
        destination,
        ignore=shutil.ignore_patterns(*sorted(IGNORED_NAMES), "*.pyc"),
    )
    return destination


def _overlay(overlay: Path, destination: Path) -> None:
    shutil.copytree(
        overlay,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*sorted(IGNORED_NAMES), "*.pyc"),
    )


def _diff_hash(baseline: Mapping[str, str], candidate: Mapping[str, str]) -> str:
    names = sorted(set(baseline) | set(candidate))
    return canonical_hash(
        [
            {"path": name, "before": baseline.get(name), "after": candidate.get(name)}
            for name in names
            if baseline.get(name) != candidate.get(name)
        ]
    )


def _diff(
    baseline: Mapping[str, str],
    candidate: Mapping[str, str],
    baseline_root: Path,
    candidate_root: Path,
) -> _DiffSummary:
    summary = _DiffSummary()
    for name in sorted(set(baseline) | set(candidate)):
        before = baseline.get(name)
        after = candidate.get(name)
        if before == after:
            continue
        if before is None:
            summary.added.append(name)
        elif after is None:
            summary.deleted.append(name)
        else:
            summary.modified.append(name)
        added, removed = _line_delta(
            _text_lines(baseline_root / name) if before is not None else [],
            _text_lines(candidate_root / name) if after is not None else [],
        )
        summary.lines_added += added
        summary.lines_removed += removed
    return summary


def _text_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _line_delta(before: Sequence[str], after: Sequence[str]) -> tuple[int, int]:
    added = 0
    removed = 0
    for line in difflib.unified_diff(before, after, n=0, lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _read_agent_report(path: Path) -> Mapping[str, object] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict) or document.get("schema_version") != "run-report-v1":
        return None
    return document


def _write_jsonl(path: Path, runs: Sequence[RunResult], campaign_id: str) -> None:
    if path.exists():
        raise CampaignError(f"output: {path.name} already exists and must not be overwritten")
    lines = [
        json.dumps(run_document(item, campaign_id=campaign_id), ensure_ascii=False, sort_keys=True)
        for item in runs
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _agent_commit() -> str | None:
    repository = Path(__file__).resolve().parents[3]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed git argv
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    commit = completed.stdout.strip()
    return commit or None


def _oracle_environment(home: Path) -> dict[str, str]:
    environ = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in _LOCALE_ENV_NAMES:
        if name in os.environ:
            environ[name] = os.environ[name]
    return environ


def _setup_detail(
    *,
    errored: bool,
    baseline_failed: bool,
    baseline_regression_passed: bool,
    gold_passed: bool,
    gold_regression_passed: bool,
    error_variant_failed: bool | None,
) -> str | None:
    if errored:
        return "an oracle exited with a reserved code during setup verification"
    if not baseline_failed:
        return "the baseline must fail the target oracle before the agent runs"
    if not baseline_regression_passed:
        return "the baseline must already pass its regression oracle"
    if not gold_passed:
        return "the gold overlay must pass the target oracle"
    if not gold_regression_passed:
        return "the gold overlay must pass the regression oracle"
    if error_variant_failed is False:
        return "the error variant must fail the target oracle"
    return None


def _elapsed_ms(started: float, finished: float) -> int:
    return max(0, round((finished - started) * 1000))


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


__all__ = [
    "AGENT_ARGV_OPTIONS",
    "AgentInvocation",
    "AgentLauncher",
    "AgentProcessResult",
    "CampaignError",
    "CampaignPlan",
    "CampaignResult",
    "JudgeHook",
    "OracleRun",
    "SetupVerification",
    "build_judge_hook",
    "launch_agent",
    "resolve_agent_executable",
    "run_campaign",
    "run_oracle",
    "verify_task_setup",
]
