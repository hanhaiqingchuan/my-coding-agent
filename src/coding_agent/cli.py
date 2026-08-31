"""Synchronous argparse entry point for browser and headless execution."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from coding_agent.config import ConfigurationError, load_settings
from coding_agent.main import RuntimeDependencies, load_command_policy, run_headless, serve_web
from coding_agent.tools.paths import WorkspacePathError

DEFAULT_CONFIG_PATH = Path("config.toml")
"""Read from the startup directory when ``--config`` is absent, as documented in README §3.

A missing file is never replaced by built-in defaults: ``load_settings`` raises a
``ConfigurationError`` naming this path, which :func:`main` reports as ``CONFIG_ERROR``.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coding-agent")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="start the local browser service")
    serve.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    serve.add_argument("--workspace", type=Path)
    serve.add_argument("--data-dir", type=Path)
    serve.add_argument("--port", type=int)
    serve.add_argument(
        "--eval-results",
        type=Path,
        dest="eval_results",
        help="read-only evaluation results root (default: <data-dir>/evaluation-results)",
    )
    serve.add_argument("--open", action="store_true", dest="open_browser", default=None)
    serve.add_argument("--yes", action="store_true")

    run = commands.add_parser("run", help="run one prompt without browser delivery")
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument("--data-dir", type=Path, required=True)
    run.add_argument("--prompt-file", type=Path, required=True)
    run.add_argument("--report-out", type=Path, required=True)
    run.add_argument("--yes", action="store_true")
    run.add_argument("--ack-unsafe-auto-approve", action="store_true")
    run.add_argument("--command-policy", type=Path)
    return parser


def main(
    argv: Sequence[str] | None = None,
    dependencies: RuntimeDependencies | None = None,
) -> int:
    """Parse synchronously and enter the asynchronous composition root once."""
    args = build_parser().parse_args(argv)
    try:
        overrides: dict[str, object] = {}
        if args.command == "serve":
            if args.port is not None:
                overrides["server.port"] = args.port
            if args.open_browser is not None:
                overrides["server.open_browser"] = args.open_browser
        settings = load_settings(args.config, overrides, os.environ)
        if args.command == "run":
            policy = _headless_policy(args)
            return asyncio.run(
                run_headless(
                    args.workspace,
                    args.data_dir,
                    args.prompt_file,
                    args.report_out,
                    settings,
                    dependencies=dependencies,
                    auto_approve=args.yes,
                    command_policy=policy,
                )
            )
        return serve_web(
            settings=settings,
            workspace=args.workspace,
            data_dir=args.data_dir,
            dependencies=dependencies,
            auto_approve=args.yes,
            evaluation_results_root=args.eval_results,
        )
    except (ConfigurationError, WorkspacePathError) as error:
        print(f"CONFIG_ERROR: {error}", file=sys.stderr)
        return 2


def _headless_policy(args: argparse.Namespace):
    if args.yes and not args.ack_unsafe_auto_approve:
        raise ConfigurationError("run --yes requires --ack-unsafe-auto-approve")
    policy = (
        load_command_policy(args.command_policy, args.workspace)
        if args.command_policy is not None
        else None
    )
    if args.yes and policy is not None and not policy.allowed:
        raise ConfigurationError("run --yes requires a non-empty command policy")
    return policy


if __name__ == "__main__":  # pragma: no cover - console script calls main directly
    raise SystemExit(main())


__all__ = ["DEFAULT_CONFIG_PATH", "build_parser", "main"]
