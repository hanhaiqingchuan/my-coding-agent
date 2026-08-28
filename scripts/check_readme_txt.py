#!/usr/bin/env python3
"""Check the ``README.txt`` deliverable against the constraints in the delivery brief.

Usage::

    uv run --python 3.12 scripts/check_readme_txt.py README.txt

Section 20 of ``doc/项目设计方案.md`` requires the ``README.txt`` deliverable to stay within 1000
characters, to carry the public repository URL, the shortest runnable procedure and a feature
summary, and to contain no API key. This script enforces exactly those constraints and reports
each violation against the field it belongs to, so the author can fix everything in one pass.

The body is measured in Unicode code points, not bytes and not words, because the delivery is
written in Chinese and a byte count would silently reject a conforming README.

Exit codes: ``0`` clean, ``1`` at least one violation, ``2`` the file could not be read.

The script depends on nothing but the standard library and reuses the credential rules from
``audit_public.py`` so the two gates cannot drift apart.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

MAX_CODE_POINTS = 1000

EXIT_CLEAN = 0
EXIT_VIOLATIONS = 1
EXIT_UNREADABLE = 2

_REPOSITORY_URL = re.compile(
    r"https://(?:github|gitee|gitlab)\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+"
)

# The shortest runnable procedure needs a command the reader can copy and the name of the
# environment variable that supplies the model credential; either alone is not runnable.
_RUN_COMMANDS = (
    "make install",
    "make start",
    "make build",
    "make check",
    "uv run",
    "npm --prefix web",
)
_RUN_ENVIRONMENT_VARIABLE = "ANTHROPIC_API_KEY"

# A feature summary has to name at least three distinct capabilities of the delivery. The table is
# part of the contract, so it is printed by --help and kept small enough to satisfy in one line.
_FEATURE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("messages_protocol", ("messages", "消息协议", "流式", "streaming")),
    ("agent_loop", ("agent loop", "代理循环", "主循环")),
    ("approval", ("审批", "approval")),
    ("context", ("上下文", "context")),
    ("tools", ("工具", "tool")),
    ("persistence", ("sqlite", "持久化", "恢复", "persistence")),
    ("web_ui", ("网页", "前端", "web")),
    ("evaluation", ("评测", "evaluation")),
)
_MINIMUM_FEATURE_GROUPS = 3


@dataclass(frozen=True)
class Violation:
    """One failed requirement, scoped to the README field that has to change."""

    field: str
    message: str

    def render(self) -> str:
        return f"VIOLATION field={self.field} :: {self.message}"


def _credential_findings(text: str) -> list[tuple[str, int, str]]:
    """Delegate to the audit script's credential rules, which live next to this file."""
    scripts_directory = str(Path(__file__).resolve().parent)
    if scripts_directory not in sys.path:
        sys.path.insert(0, scripts_directory)
    from audit_public import credential_findings

    return credential_findings(text)


def read_body(path: Path) -> str:
    """Return the deliverable body with normalized line endings and no outer whitespace."""
    content = path.read_text(encoding="utf-8")
    return content.replace("\r\n", "\n").replace("\r", "\n").strip()


def check_body(body: str, expected_url: str | None = None) -> list[Violation]:
    """Return every violation in ``body``, ordered by field so the output stays diffable."""
    violations: list[Violation] = []

    if len(body) > MAX_CODE_POINTS:
        violations.append(
            Violation(
                "body_length",
                f"body is {len(body)} code points, the limit is {MAX_CODE_POINTS}",
            )
        )

    if expected_url is not None:
        if expected_url not in body:
            violations.append(
                Violation("repository_url", "the pinned public repository URL is missing")
            )
    elif not _REPOSITORY_URL.search(body):
        violations.append(
            Violation("repository_url", "no public repository URL was found in the body")
        )

    lowered = body.lower()
    missing_run_parts: list[str] = []
    if not any(command in lowered for command in _RUN_COMMANDS):
        commands = " or ".join(repr(command) for command in _RUN_COMMANDS)
        missing_run_parts.append(f"a runnable command ({commands})")
    if _RUN_ENVIRONMENT_VARIABLE not in body:
        missing_run_parts.append(f"the {_RUN_ENVIRONMENT_VARIABLE} environment variable name")
    if missing_run_parts:
        violations.append(
            Violation(
                "run_procedure",
                "the shortest runnable procedure is missing " + " and ".join(missing_run_parts),
            )
        )

    matched_groups = [
        name
        for name, keywords in _FEATURE_GROUPS
        if any(keyword in lowered for keyword in keywords)
    ]
    if len(matched_groups) < _MINIMUM_FEATURE_GROUPS:
        violations.append(
            Violation(
                "feature_summary",
                f"only {len(matched_groups)} of the {_MINIMUM_FEATURE_GROUPS} required feature "
                "topics were found",
            )
        )

    for rule_id, line_number, _title in _credential_findings(body):
        violations.append(
            Violation("credentials", f"credential pattern {rule_id} at line {line_number}")
        )

    return violations


def _summary(body: str, violations: Sequence[Violation]) -> str:
    status = "fail" if violations else "clean"
    fields = ",".join(sorted({violation.field for violation in violations})) or "-"
    return (
        f"README_TXT_SUMMARY status={status} violations={len(violations)} "
        f"code_points={len(body)} limit={MAX_CODE_POINTS} fields={fields}"
    )


def _build_parser() -> argparse.ArgumentParser:
    topics = ", ".join(name for name, _ in _FEATURE_GROUPS)
    parser = argparse.ArgumentParser(
        prog="check_readme_txt.py",
        description=(
            "Check the README.txt deliverable: at most 1000 Unicode code points, a public "
            "repository URL, the shortest runnable procedure, a feature summary and no credential."
        ),
        epilog=(
            f"At least {_MINIMUM_FEATURE_GROUPS} of these feature topics must be named: {topics}. "
            "Exit codes: 0 clean, 1 violations, 2 the file could not be read."
        ),
    )
    parser.add_argument("path", help="path to the README.txt deliverable")
    parser.add_argument(
        "--expect-url",
        dest="expect_url",
        default=None,
        help="require this exact public repository URL instead of any recognized host",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_request:
        return EXIT_UNREADABLE if exit_request.code else EXIT_CLEAN

    path = Path(args.path)
    try:
        body = read_body(path)
    except (OSError, UnicodeDecodeError) as error:
        print(f"check_readme_txt: cannot read {path}: {error}", file=sys.stderr)
        return EXIT_UNREADABLE

    violations = check_body(body, args.expect_url)
    for violation in violations:
        print(violation.render())
    print(_summary(body, violations))
    return EXIT_VIOLATIONS if violations else EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
