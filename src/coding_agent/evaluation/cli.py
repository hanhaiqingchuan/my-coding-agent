"""The ``coding-agent-eval`` entry point.

This module only parses arguments and prints operator-facing text. Validation lives in
``manifest``, orchestration in ``runner``, scoring and serialization in ``report``,
the fuzzy-metric judge in ``judge``, and the campaign index in ``history``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from coding_agent.evaluation.history import HistoryError, format_history, scan_campaigns
from coding_agent.evaluation.manifest import ManifestError, validate_manifest
from coding_agent.evaluation.report import ReportError, summarize_campaign
from coding_agent.evaluation.runner import (
    CampaignError,
    build_judge_hook,
    run_campaign,
    verify_task_setup,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coding-agent-eval")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate a manifest and verify every task")
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument(
        "--static-only",
        action="store_true",
        help="skip baseline, gold and error-variant verification",
    )

    run = commands.add_parser("run", help="run one campaign through the public headless CLI")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--config", type=Path, default=Path("config.toml"))
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument(
        "--serial",
        action="store_true",
        help="run tasks one at a time; P0 never runs a campaign in parallel",
    )
    run.add_argument("--dry-run", action="store_true")
    run.add_argument(
        "--judge",
        action="store_true",
        help="score every finished run with the LLM judge after it completes",
    )

    summarize = commands.add_parser("summarize", help="aggregate one campaign's run records")
    summarize.add_argument("--input", type=Path, required=True)
    summarize.add_argument("--out", type=Path)

    history = commands.add_parser(
        "history", help="print the campaign history index for a results root"
    )
    history.add_argument("--results", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one evaluation command and translate failures into exit codes."""
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            return _validate(args.manifest, static_only=args.static_only)
        if args.command == "run":
            return _run(args)
        if args.command == "history":
            return _history(args.results)
        return _summarize(args.input, args.out)
    except ManifestError as error:
        print(f"MANIFEST_ERROR: {error}", file=sys.stderr)
        return 2
    except (CampaignError, ReportError, HistoryError) as error:
        print(f"EVALUATION_ERROR: {error}", file=sys.stderr)
        return 2


def _validate(manifest_path: Path, *, static_only: bool) -> int:
    manifest = validate_manifest(manifest_path)
    print(f"manifest {manifest.manifest_id}: {len(manifest.tasks)} task(s) validated")
    if static_only:
        return 0
    failures = 0
    with TemporaryDirectory(prefix="coding-agent-eval-") as scratch:
        for task in manifest.tasks:
            verification = verify_task_setup(task, scratch=Path(scratch) / task.task_id)
            status = "ok" if verification.ok else f"HARNESS_SETUP: {verification.detail}"
            print(f"  {task.task_id} ({task.category}): {status}")
            failures += 0 if verification.ok else 1
    return 0 if failures == 0 else 1


def _run(args: argparse.Namespace) -> int:
    manifest = validate_manifest(args.manifest)
    judge = build_judge_hook(args.config) if args.judge and not args.dry_run else None
    result = run_campaign(
        manifest,
        args.config,
        args.repeats,
        args.out,
        args.dry_run,
        judge=judge,
    )
    if result.dry_run:
        assert result.plan is not None
        print(f"dry run for manifest {manifest.manifest_id} (no model call is made)")
        for line in result.plan.lines():
            print(f"  {line}")
        return 0
    summary = result.summary
    assert summary is not None
    print(f"campaign {result.campaign_id}: {summary.started_runs} run(s) started")
    print(f"  valid runs: {summary.valid_runs}")
    print(f"  strict successes: {summary.strict_success_runs}")
    print(f"  harness setup outcomes: {summary.harness_setup_runs}")
    print(f"  harness oracle errors: {summary.harness_oracle_error_runs}")
    if summary.judged_runs:
        print(f"  judged runs: {summary.judged_runs} (judge errors: {summary.judge_error_runs})")
    print(f"  results: {result.output_dir}")
    complete = summary.valid_runs == summary.started_runs
    return 0 if complete and summary.strict_success_runs == summary.valid_runs else 1


def _history(results: Path) -> int:
    summaries = scan_campaigns(results)
    for line in format_history(summaries):
        print(line)
    return 0


def _summarize(input_dir: Path, output_dir: Path | None) -> int:
    summary = summarize_campaign(input_dir, output_dir or input_dir / "reports")
    print(f"summarized {summary.started_runs} run(s) from {input_dir}")
    print(f"  strict successes: {summary.strict_success_runs}/{summary.valid_runs}")
    return 0


if __name__ == "__main__":  # pragma: no cover - console script calls main directly
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
