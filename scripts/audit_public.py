#!/usr/bin/env python3
"""Audit the public delivery repository before it is published.

Usage::

    uv run --python 3.12 scripts/audit_public.py --repo . --history

The script implements section 19 ("安全与公开仓库边界") of ``doc/项目设计方案.md``. It scans the
tracked working tree and, with ``--history``, every blob reachable from the Git history for four
rule families: credential patterns, forbidden tracked paths, internal traces and second-protocol
residue.

A finding reports only the rule identifier and a location. The matched text is never printed,
because a match may itself be the secret the audit exists to find.

Exit codes:

* ``0`` no findings.
* ``1`` at least one working-tree finding.
* ``2`` no working-tree finding, but at least one Git-history finding.
* ``3`` usage error, or Git could not be queried.

The working-tree scan and the history scan are counted and printed separately. A historical
finding that cannot be removed without rewriting published history therefore yields the distinct
exit code ``2`` and never masks a new working-tree regression, which would exit ``1``.

The script deliberately depends on nothing but the standard library and the ``git`` executable, so
that it can run as a release gate even when the application package is not installed.
"""

from __future__ import annotations

import argparse
import functools
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

SCOPE_TREE = "tree"
SCOPE_HISTORY = "history"
_SCOPE_ORDER = {SCOPE_TREE: 0, SCOPE_HISTORY: 1}

EXIT_CLEAN = 0
EXIT_TREE_FINDINGS = 1
EXIT_HISTORY_FINDINGS = 2
EXIT_USAGE = 3

_ABBREV = 12
_BATCH_SIZE = 128


class GitError(RuntimeError):
    """Raised when a Git query fails or the target directory is not a repository."""


@dataclass(frozen=True)
class Finding:
    """One rule hit. ``line`` is ``0`` for path rules; ``commit`` is empty for the tree scan."""

    scope: str
    rule_id: str
    path: str
    line: int
    commit: str
    title: str

    @property
    def sort_key(self) -> tuple[int, str, str, int, str]:
        return (_SCOPE_ORDER[self.scope], self.rule_id, self.path, self.line, self.commit)

    def render(self) -> str:
        location = self.path if self.line == 0 else f"{self.path}:{self.line}"
        if self.commit:
            location = f"{location}@{self.commit}"
        return f"{self.scope} {self.rule_id} {location} :: {self.title}"


@dataclass(frozen=True)
class PathRule:
    """A path that must never be tracked in the public repository."""

    rule_id: str
    title: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class ContentRule:
    """A line pattern that must not appear in a tracked or historical blob.

    ``readme_allows_unsupported_note`` and ``design_documents_allowed`` encode the only two
    content allowances the delivery grants. Both are opt-in per rule so that a new rule never
    inherits an allowance by accident.
    """

    rule_id: str
    title: str
    pattern: re.Pattern[str]
    readme_allows_unsupported_note: bool = False
    design_documents_allowed: bool = False


# --- allowances ------------------------------------------------------------------------------
#
# Section 19 publishes exactly two documents under `doc/`, and section 20 requires the two README
# deliverables to describe the delivery scope. Those files therefore have to be able to name the
# second protocol that the delivery does not implement. Both allowances are narrow: they apply to
# an exact set of paths, only to the rules that opt in below, and for the README deliverables only
# on a line that also states that the protocol is unsupported.

_README_DELIVERABLES = frozenset({"README.md", "README.txt"})

_UNSUPPORTED_PROTOCOL_MARKERS = (
    "不支持",
    "第二协议",
    "unsupported",
    "not supported",
    "does not support",
)


def _is_public_design_document(path: str) -> bool:
    """True only for a Markdown document directly inside ``doc/``."""
    return path.startswith("doc/") and path.endswith(".md") and path.count("/") == 1


def _is_allowed(rule: ContentRule, path: str, line: str) -> bool:
    if rule.design_documents_allowed and _is_public_design_document(path):
        return True
    if rule.readme_allows_unsupported_note and path in _README_DELIVERABLES:
        lowered = line.lower()
        return any(marker.lower() in lowered for marker in _UNSUPPORTED_PROTOCOL_MARKERS)
    return False


# --- rule tables -----------------------------------------------------------------------------

_CREDENTIAL_VALUE = (
    r"(?P<quote>[\"'])"
    r"(?![^\"'\n]*(?:\$|\{|<|your|example|placeholder|redacted|changeme|dummy|fake|sample"
    r"|todo|xxx|\.\.\.|environ|getenv))"
    r"(?=[^\"'\n]*[0-9])"
    r"(?=[^\"'\n]*[A-Za-z])"
    r"[A-Za-z0-9_\-./+=]{12,}"
    r"(?P=quote)"
)

CREDENTIAL_RULES: tuple[ContentRule, ...] = (
    ContentRule(
        "CRED001",
        "credential pattern: provider API key literal",
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{12,}|\bsk-[A-Za-z0-9]{32,}"),
    ),
    ContentRule(
        "CRED002",
        "credential pattern: secret assigned to a literal value",
        re.compile(
            r"(?i)\b(?:api[_-]?keys?|secret(?:[_-]?keys?)?|access[_-]?tokens?"
            r"|auth[_-]?tokens?|client[_-]?secret|tokens?|passwords?|passwd|pwd)\b"
            r"[ \t]*[:=]{1,2}[ \t]*" + _CREDENTIAL_VALUE
        ),
    ),
    ContentRule(
        "CRED003",
        "credential pattern: authorization header carrying a literal credential",
        re.compile(
            r"(?i)\bauthorization\b[ \t]*[:=][ \t]*[\"']?[ \t]*"
            r"(?:bearer|basic|token)[ \t]+[A-Za-z0-9._\-+/=]{8,}"
        ),
    ),
    ContentRule(
        "CRED004",
        "credential pattern: PEM private key block",
        re.compile(r"-----BEGIN(?:[ ][A-Z0-9]+)*[ ]PRIVATE KEY-----"),
    ),
)

PATH_RULES: tuple[PathRule, ...] = (
    PathRule(
        "PATH001",
        "forbidden tracked path: private planning workspace",
        re.compile(r"(?:^|/)\.superpowers/"),
    ),
    PathRule(
        "PATH002",
        "forbidden tracked path: local Git worktree",
        re.compile(r"(?:^|/)\.worktrees/"),
    ),
    PathRule(
        "PATH003",
        "forbidden tracked path: scratch directory",
        re.compile(r"(?:^|/)tmp/"),
    ),
    PathRule(
        "PATH004",
        "forbidden tracked path: local configuration file",
        re.compile(r"^config\.toml$"),
    ),
    PathRule(
        "PATH005",
        "forbidden tracked path: environment file",
        re.compile(r"^(?:.*/)?\.env(?:\.(?!example$)[^/]+)?$"),
    ),
    PathRule(
        "PATH006",
        "forbidden tracked path: application database",
        re.compile(r"\.(?:db|sqlite[0-9]*)(?:-[A-Za-z]+)?$"),
    ),
    PathRule(
        "PATH007",
        "forbidden tracked path: log file",
        re.compile(r"\.log$"),
    ),
    PathRule(
        "PATH008",
        "forbidden tracked path: frontend build output",
        re.compile(r"^web/dist/"),
    ),
    PathRule(
        "PATH009",
        "forbidden tracked path: installed dependencies",
        re.compile(r"(?:^|/)node_modules/"),
    ),
    PathRule(
        "PATH010",
        "forbidden tracked path: test report output",
        re.compile(r"(?:^|/)(?:playwright-report|test-results)/"),
    ),
    PathRule(
        "PATH011",
        "forbidden tracked path: Python virtual environment",
        re.compile(r"(?:^|/)\.venv/"),
    ),
    PathRule(
        "PATH012",
        "forbidden tracked path: key material file",
        re.compile(r"\.(?:pem|key|p12|pfx|jks|keystore)$"),
    ),
    PathRule(
        "PATH013",
        "forbidden tracked path: unpublished reference material",
        re.compile(r"^doc/ref/"),
    ),
)

TRACE_RULES: tuple[ContentRule, ...] = (
    ContentRule(
        "TRACE001",
        "internal trace: local absolute home directory path",
        re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/"),
    ),
    ContentRule(
        "TRACE002",
        "internal trace: internal-looking hostname or domain",
        re.compile(r"(?i)\b[A-Za-z0-9-]+\.(?:internal|intranet|corp|lan|localdomain)\b"),
    ),
    ContentRule(
        "TRACE003",
        "internal trace: private-network URL",
        re.compile(
            r"(?i)\bhttps?://(?:10\.|192\.168\.|172\.(?:1[6-9]|2[0-9]|3[01])\.)",
        ),
    ),
    ContentRule(
        "TRACE004",
        "internal trace: staff identifier value",
        re.compile(
            r"(?i)\b(?:employee|staff)[ _-]?(?:id|no|number|code)\b[ \t]*[:=#]{0,2}[ \t]*"
            r"[\"']?[A-Za-z]{0,3}[0-9]{3,}"
            r"|(?:工号|员工编号|员工号|工作证号)[ \t]*[:：=]?[ \t]*[A-Za-z]{0,3}[0-9]{3,}"
        ),
    ),
)

# The delivery ships a single Anthropic-compatible Messages implementation, so the competing
# vendor's package, client class and request/response field names must not reach the public
# repository as code, dependency or configuration.
#
# Every needle below is split across adjacent string literals. The fragments are concatenated at
# import time, so the token never appears in this file's own text and these rules apply to this
# script exactly as they apply to every other tracked file. That keeps the rule family free of a
# self-exemption, which would be a hole an auditor could not see.
SECOND_PROTOCOL_RULES: tuple[ContentRule, ...] = (
    ContentRule(
        "PROTO001",
        "second-protocol residue: vendor package imported",
        re.compile(
            r"^[ \t]*(?:import|from)[ \t]+open"
            r"ai\b"
            r"|(?:from|import|require[ \t]*\()[ \t]*[\"']open"
            r"ai[\"']"
        ),
    ),
    ContentRule(
        "PROTO002",
        "second-protocol residue: vendor package declared as a dependency",
        re.compile(
            r"[\"']open"
            r"ai(?:[=<>~!^@]|[\"'][ \t]*:[ \t]*[\"']?[\^~><=0-9])"
            r"|^[ \t]*name[ \t]*=[ \t]*[\"']open"
            r"ai[\"']"
        ),
    ),
    ContentRule(
        "PROTO003",
        "second-protocol residue: vendor async client class",
        re.compile(r"\bAsync" r"Open" r"AI\b"),
        design_documents_allowed=True,
    ),
    ContentRule(
        "PROTO004",
        "second-protocol residue: completions endpoint path",
        re.compile(r"\bchat" r"\.completions\b"),
        design_documents_allowed=True,
    ),
    ContentRule(
        "PROTO005",
        "second-protocol residue: stop-reason response field",
        re.compile(r"\bfinish" r"_reason\b"),
        design_documents_allowed=True,
    ),
    ContentRule(
        "PROTO006",
        "second-protocol residue: stream request field",
        re.compile(r"\bstream" r"_options\b"),
        design_documents_allowed=True,
    ),
    ContentRule(
        "PROTO007",
        "second-protocol residue: output-token request field",
        re.compile(r"\bmax" r"_completion_tokens\b"),
        design_documents_allowed=True,
    ),
    ContentRule(
        "PROTO008",
        "second-protocol residue: vendor name outside the documented allowance",
        re.compile(r"(?i)\bopen" r"ai\b"),
        readme_allows_unsupported_note=True,
        design_documents_allowed=True,
    ),
)

CONTENT_RULES: tuple[ContentRule, ...] = CREDENTIAL_RULES + TRACE_RULES + SECOND_PROTOCOL_RULES


def credential_findings(text: str) -> list[tuple[str, int, str]]:
    """Return ``(rule_id, line_number, title)`` for every credential pattern in ``text``.

    Exposed so that the README deliverable check enforces the same credential rules instead of
    maintaining a second, drifting copy of them.
    """
    hits: list[tuple[str, int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for rule in CREDENTIAL_RULES:
            if rule.pattern.search(line):
                hits.append((rule.rule_id, number, rule.title))
    return hits


# --- Git access ------------------------------------------------------------------------------


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args)} failed: {detail or completed.returncode}")
    return completed.stdout


def _decode_path(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _tracked_paths(repo: Path) -> list[str]:
    raw = _git(repo, "ls-files", "-z")
    return sorted({_decode_path(entry) for entry in raw.split(b"\0") if entry})


def _top_level(repo: Path) -> Path:
    """Return the repository root, so reported paths are always repository-relative."""
    raw = _git(repo, "rev-parse", "--show-toplevel").decode("utf-8", "surrogateescape").strip()
    return Path(raw) if raw else repo


def _read_tracked_bytes(repo: Path, relative: str) -> bytes | None:
    target = repo / relative
    try:
        if target.is_symlink():
            return os.readlink(target).encode("utf-8", "surrogateescape")
        if not target.is_file():
            return None
        return target.read_bytes()
    except OSError:
        return None


def _history_object_paths(repo: Path, include_reflog: bool) -> dict[str, set[str]]:
    args = ["rev-list", "--objects", "--all"]
    if include_reflog:
        args.append("--reflog")
    mapping: dict[str, set[str]] = {}
    for raw in _git(repo, *args).split(b"\n"):
        if not raw:
            continue
        parts = raw.split(b" ", 1)
        if len(parts) != 2 or not parts[1]:
            continue
        mapping.setdefault(parts[0].decode("ascii", "replace"), set()).add(_decode_path(parts[1]))
    return mapping


def _chunks(values: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _cat_file_batch(repo: Path, shas: Sequence[str]) -> Iterator[tuple[str, str, bytes]]:
    data = _git(repo, "cat-file", "--batch", stdin=("\n".join(shas) + "\n").encode("ascii"))
    offset = 0
    for _ in shas:
        end = data.find(b"\n", offset)
        if end == -1:
            return
        header = data[offset:end].decode("utf-8", "replace").split()
        offset = end + 1
        if len(header) < 3:
            continue
        size = int(header[2])
        yield header[0], header[1], data[offset : offset + size]
        offset += size + 1


@functools.lru_cache(maxsize=None)
def _commit_introducing_path(repo: Path, path: str, include_reflog: bool) -> str:
    args = ["log", "--all", "--reverse", f"--abbrev={_ABBREV}", "--format=%h", "--diff-filter=A"]
    if include_reflog:
        args.append("--reflog")
    lines = _git(repo, *args, "--", path).decode("utf-8", "replace").split()
    return lines[0] if lines else ""


@functools.lru_cache(maxsize=None)
def _commit_introducing_blob(repo: Path, sha: str, include_reflog: bool) -> str:
    args = [
        "log",
        "--all",
        "--reverse",
        f"--abbrev={_ABBREV}",
        "--format=%h",
        f"--find-object={sha}",
    ]
    if include_reflog:
        args.append("--reflog")
    lines = _git(repo, *args).decode("utf-8", "replace").split()
    return lines[0] if lines else f"blob:{sha[:_ABBREV]}"


# --- scanning --------------------------------------------------------------------------------


def _path_findings(scope: str, path: str, commit: str) -> Iterator[Finding]:
    for rule in PATH_RULES:
        if rule.pattern.search(path):
            yield Finding(scope, rule.rule_id, path, 0, commit, rule.title)


def _content_findings(scope: str, path: str, blob: bytes, commit: str) -> Iterator[Finding]:
    if b"\0" in blob:
        return
    text = blob.decode("utf-8", "replace")
    for number, line in enumerate(text.splitlines(), start=1):
        for rule in CONTENT_RULES:
            if rule.pattern.search(line) and not _is_allowed(rule, path, line):
                yield Finding(scope, rule.rule_id, path, number, commit, rule.title)


def scan_working_tree(repo: Path) -> list[Finding]:
    """Scan every tracked path and its on-disk content."""
    findings: list[Finding] = []
    for path in _tracked_paths(repo):
        findings.extend(_path_findings(SCOPE_TREE, path, ""))
        blob = _read_tracked_bytes(repo, path)
        if blob is not None:
            findings.extend(_content_findings(SCOPE_TREE, path, blob, ""))
    return findings


def scan_history(repo: Path, include_reflog: bool = False) -> list[Finding]:
    """Scan every blob reachable from the Git history, at every path it was stored under."""
    mapping = _history_object_paths(repo, include_reflog)
    findings: list[Finding] = []
    blob_paths: set[str] = set()
    shas = sorted(mapping)
    for chunk in _chunks(shas, _BATCH_SIZE):
        for sha, object_type, blob in _cat_file_batch(repo, chunk):
            if object_type != "blob":
                continue
            for path in sorted(mapping[sha]):
                blob_paths.add(path)
                for finding in _content_findings(SCOPE_HISTORY, path, blob, ""):
                    commit = _commit_introducing_blob(repo, sha, include_reflog)
                    findings.append(_with_commit(finding, commit))

    # A forbidden path is one finding regardless of how many versions of the file exist, so the
    # path rules run once per distinct historical path rather than once per blob.
    for path in sorted(blob_paths):
        for finding in _path_findings(SCOPE_HISTORY, path, ""):
            commit = _commit_introducing_path(repo, path, include_reflog)
            findings.append(_with_commit(finding, commit))
    return findings


def _with_commit(finding: Finding, commit: str) -> Finding:
    return Finding(
        finding.scope, finding.rule_id, finding.path, finding.line, commit, finding.title
    )


def _summary(findings: Iterable[Finding], history_scanned: bool, reflog_scanned: bool) -> str:
    collected = list(findings)
    tree = sum(1 for finding in collected if finding.scope == SCOPE_TREE)
    history = len(collected) - tree
    if tree:
        status = "fail"
    elif history:
        status = "history_only"
    else:
        status = "clean"
    rules = ",".join(sorted({finding.rule_id for finding in collected})) or "-"
    return (
        f"AUDIT_SUMMARY status={status} findings={len(collected)} tree={tree} history={history} "
        f"history_scanned={str(history_scanned).lower()} "
        f"reflog_scanned={str(reflog_scanned).lower()} rules={rules}"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit_public.py",
        description=(
            "Audit the public delivery repository for credentials, forbidden paths, internal "
            "traces and second-protocol residue. Reports rule identifiers and locations only."
        ),
        epilog=(
            "Exit codes: 0 clean, 1 working-tree findings, 2 history-only findings, "
            "3 usage or Git error."
        ),
    )
    parser.add_argument("--repo", default=".", help="repository to audit (default: .)")
    parser.add_argument(
        "--history",
        action="store_true",
        help="also scan every blob reachable from the Git history",
    )
    parser.add_argument(
        "--reflog",
        action="store_true",
        help="extend --history to objects that are only reachable from the reflog",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_request:
        return EXIT_USAGE if exit_request.code else EXIT_CLEAN

    repo = Path(args.repo).resolve()
    include_reflog = bool(args.reflog)
    history_scanned = bool(args.history or include_reflog)
    try:
        if not repo.is_dir():
            raise GitError(f"not a directory: {repo}")
        _git(repo, "rev-parse", "--git-dir")
        repo = _top_level(repo)
        findings = scan_working_tree(repo)
        if history_scanned:
            findings.extend(scan_history(repo, include_reflog))
    except GitError as error:
        print(f"audit_public: {error}", file=sys.stderr)
        return EXIT_USAGE

    findings = sorted(set(findings), key=lambda finding: finding.sort_key)
    for finding in findings:
        print(finding.render())
    print(_summary(findings, history_scanned, include_reflog))

    if any(finding.scope == SCOPE_TREE for finding in findings):
        return EXIT_TREE_FINDINGS
    if findings:
        return EXIT_HISTORY_FINDINGS
    return EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
