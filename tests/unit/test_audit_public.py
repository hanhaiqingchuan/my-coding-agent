from __future__ import annotations

import functools
import importlib.util
import io
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUDIT_SCRIPT = _REPO_ROOT / "scripts" / "audit_public.py"

# Fixture tokens are assembled from fragments and interpolated into the fixture text instead of
# being written as one literal. The audit also scans this file, so a literal fixture secret or a
# literal second-protocol token here would make the delivery repository fail its own audit, and
# section 19 of doc/项目设计方案.md forbids committing credential-shaped material at all.
_FAKE_ANTHROPIC_KEY = "sk-ant-" + "fake0" * 6
_FAKE_GENERIC_KEY = "sk-" + "fake" * 9
_FAKE_CREDENTIAL_VALUE = "a1b2c3d4e5f6g7h8"
_FAKE_BEARER_VALUE = "abc123def456ghi789"
_FAKE_PEM_KIND = "RSA"
_FAKE_PEM_BODY = "ZmFrZS1rZXktbWF0ZXJpYWw="
_FAKE_PEM_BLOCK = (
    f"-----BEGIN {_FAKE_PEM_KIND} PRIVATE KEY-----\n"
    f"{_FAKE_PEM_BODY}\n"
    f"-----END {_FAKE_PEM_KIND} PRIVATE KEY-----\n"
)
_INTERNAL_LABEL = "wiki"
_PRIVATE_HOST_A = "10.20.30.40"
_PRIVATE_HOST_B = "192.168.10.20:8080"
_LOCAL_USER = "alice"
_FAKE_EMPLOYEE_ID = "100234"

_PROTO_PACKAGE = "open" + "ai"
_PROTO_CLIENT_CLASS = "Async" + "Open" + "AI"
_PROTO_ENDPOINT = "chat" + ".completions"
_PROTO_FINISH_FIELD = "finish" + "_reason"
_PROTO_STREAM_FIELD = "stream" + "_options"
_PROTO_TOKENS_FIELD = "max" + "_completion_tokens"
_PROTO_VENDOR_NAME = "Open" + "AI"

_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "Audit Fixture",
    "GIT_AUTHOR_EMAIL": "audit@example.invalid",
    "GIT_COMMITTER_NAME": "Audit Fixture",
    "GIT_COMMITTER_EMAIL": "audit@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}


@functools.lru_cache(maxsize=1)
def _audit_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("audit_public_under_test", _AUDIT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=_GIT_ENV,
        check=True,
        capture_output=True,
    )


def _commit(repo: Path, files: Mapping[str, str], message: str) -> None:
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A", ".")
    _git(repo, "commit", "-m", message)


def _make_repo(root: Path, files: Mapping[str, str], message: str = "initial commit") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init")
    _commit(root, files, message)
    return root


def _audit(repo: Path, *extra: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = _audit_module().main(["--repo", str(repo), *extra])
    return code, stdout.getvalue(), stderr.getvalue()


def _rule_ids(output: str) -> list[str]:
    return [line.split()[1] for line in output.splitlines() if not line.startswith("AUDIT_SUMMARY")]


def test_clean_repository_exits_zero_and_reports_no_findings(tmp_path: Path) -> None:
    """A clean tree must exit zero, otherwise the release gate can never pass."""
    repo = _make_repo(
        tmp_path / "repo",
        {"src/app.py": "print('ok')\n", "README.md": "# demo\n"},
    )

    code, out, _ = _audit(repo, "--history")

    assert code == 0
    assert "AUDIT_SUMMARY status=clean findings=0 tree=0 history=0" in out
    assert _rule_ids(out) == []


@pytest.mark.parametrize(
    ("content", "rule_id"),
    [
        (f'KEY = "{_FAKE_ANTHROPIC_KEY}"\n', "CRED001"),
        (f'KEY = "{_FAKE_GENERIC_KEY}"\n', "CRED001"),
        (f'api_key = "{_FAKE_CREDENTIAL_VALUE}"\n', "CRED002"),
        (f'token: "{_FAKE_CREDENTIAL_VALUE}"\n', "CRED002"),
        (f'password="{_FAKE_CREDENTIAL_VALUE}"\n', "CRED002"),
        (f"Authorization: Bearer {_FAKE_BEARER_VALUE}\n", "CRED003"),
        (_FAKE_PEM_BLOCK, "CRED004"),
    ],
)
def test_credential_patterns_are_reported_without_echoing_the_match(
    tmp_path: Path, content: str, rule_id: str
) -> None:
    """Echoing a match would copy the secret into audit logs and CI output."""
    repo = _make_repo(tmp_path / "repo", {"src/client.py": content})

    code, out, _ = _audit(repo)

    assert code == 1
    assert rule_id in _rule_ids(out)
    assert "tree" in out
    assert "src/client.py:1" in out
    for secret in content.split():
        assert len(secret) < 8 or secret not in out


def test_placeholder_values_are_not_reported_as_credentials(tmp_path: Path) -> None:
    """Flagging documented placeholders would make the gate noisy and get it disabled."""
    repo = _make_repo(
        tmp_path / "repo",
        {
            "config.example.toml": 'api_key = "${ANTHROPIC_API_KEY}"\n',
            "docs/setup.md": 'token = "your-token-goes-here"\n',
        },
    )

    code, out, _ = _audit(repo, "--history")

    assert code == 0, out


@pytest.mark.parametrize(
    ("path", "rule_id"),
    [
        (".superpowers/sdd/plan/report.md", "PATH001"),
        (".worktrees/feature/file.txt", "PATH002"),
        ("tmp/scratch.txt", "PATH003"),
        ("config.toml", "PATH004"),
        (".env", "PATH005"),
        (".env.production", "PATH005"),
        ("data/agent.db", "PATH006"),
        ("data/agent.sqlite3", "PATH006"),
        ("logs/server.log", "PATH007"),
        ("web/dist/assets/index.js", "PATH008"),
        ("web/node_modules/left-pad/index.js", "PATH009"),
        ("playwright-report/index.html", "PATH010"),
        ("test-results/results.xml", "PATH010"),
        (".venv/lib/site.py", "PATH011"),
        ("secrets/server.pem", "PATH012"),
        ("doc/ref/original-task.md", "PATH013"),
    ],
)
def test_forbidden_tracked_paths_are_reported(tmp_path: Path, path: str, rule_id: str) -> None:
    """Section 19 keeps these artifacts out of the public repository entirely."""
    repo = _make_repo(tmp_path / "repo", {path: "placeholder\n"})

    code, out, _ = _audit(repo)

    assert code == 1
    assert rule_id in _rule_ids(out)
    assert f"tree {rule_id} {path} ::" in out


@pytest.mark.parametrize(
    "path",
    ["config.example.toml", ".env.example", "doc/项目设计方案.md", "web/src/main.tsx"],
)
def test_published_paths_are_not_reported_as_forbidden(tmp_path: Path, path: str) -> None:
    """The forbidden-path rules must not reject the files the delivery has to ship."""
    repo = _make_repo(tmp_path / "repo", {path: "placeholder\n"})

    code, out, _ = _audit(repo, "--history")

    assert code == 0, out


def test_untracked_file_with_a_credential_is_not_reported(tmp_path: Path) -> None:
    """The audit describes what the repository publishes, not the developer's scratch files."""
    repo = _make_repo(tmp_path / "repo", {"src/app.py": "print('ok')\n"})
    (repo / "scratch.txt").write_text(f'api_key = "{_FAKE_CREDENTIAL_VALUE}"\n', encoding="utf-8")

    code, out, _ = _audit(repo, "--history")

    assert code == 0, out


@pytest.mark.parametrize(
    ("content", "rule_id"),
    [
        (f"deploy to https://{_INTERNAL_LABEL}.internal.example/runbook\n", "TRACE002"),
        (f"see {_INTERNAL_LABEL}.intranet.example for details\n", "TRACE002"),
        (f"mirror at https://{_PRIVATE_HOST_A}/git/project.git\n", "TRACE003"),
        (f"mirror at https://{_PRIVATE_HOST_B}/git\n", "TRACE003"),
        (f"reviewed by employee_id = {_FAKE_EMPLOYEE_ID}\n", "TRACE004"),
        (f"工号：{_FAKE_EMPLOYEE_ID}\n", "TRACE004"),
    ],
)
def test_internal_traces_are_reported(tmp_path: Path, content: str, rule_id: str) -> None:
    """Internal hosts and staff identifiers are the traces section 19 forbids publishing."""
    repo = _make_repo(tmp_path / "repo", {"notes/handover.md": content})

    code, out, _ = _audit(repo)

    assert code == 1
    assert rule_id in _rule_ids(out)
    assert "notes/handover.md:1" in out


def test_local_absolute_home_path_is_reported(tmp_path: Path) -> None:
    """A committed developer path leaks the machine layout and the account name."""
    repo = _make_repo(
        tmp_path / "repo",
        {"docs/run.md": f"run /Users/{_LOCAL_USER}/projects/demo/main.py\n"},
    )

    code, out, _ = _audit(repo)

    assert code == 1
    assert "TRACE001" in _rule_ids(out)
    assert _LOCAL_USER not in out


def test_prose_mentioning_staff_identifiers_without_a_value_is_not_reported(
    tmp_path: Path,
) -> None:
    """The design documents have to be able to state the rule they are describing."""
    repo = _make_repo(
        tmp_path / "repo",
        {"doc/项目设计方案.md": "公开代码不得包含内部 URL、项目路径、工号或其它内部痕迹。\n"},
    )

    code, out, _ = _audit(repo, "--history")

    assert code == 0, out


@pytest.mark.parametrize(
    ("path", "content", "rule_id"),
    [
        ("src/client.py", f"import {_PROTO_PACKAGE}\n", "PROTO001"),
        ("src/client.py", f"from {_PROTO_PACKAGE} import client\n", "PROTO001"),
        ("web/src/model.ts", f'import client from "{_PROTO_PACKAGE}";\n', "PROTO001"),
        ("pyproject.toml", f'dependencies = ["{_PROTO_PACKAGE}==1.2.3"]\n', "PROTO002"),
        ("web/package.json", f'{{"dependencies": {{"{_PROTO_PACKAGE}": "^4.0.0"}}}}\n', "PROTO002"),
        ("uv.lock", f'name = "{_PROTO_PACKAGE}"\n', "PROTO002"),
        ("src/client.py", f"client = {_PROTO_CLIENT_CLASS}()\n", "PROTO003"),
        ("src/client.py", f"await client.{_PROTO_ENDPOINT}.create()\n", "PROTO004"),
        ("src/client.py", f"if choice.{_PROTO_FINISH_FIELD} == 'stop':\n", "PROTO005"),
        ("src/client.py", f"{_PROTO_STREAM_FIELD} = {{'include_usage': True}}\n", "PROTO006"),
        ("src/client.py", f"{_PROTO_TOKENS_FIELD} = 1024\n", "PROTO007"),
        ("src/client.py", f"# fall back to the {_PROTO_VENDOR_NAME} service\n", "PROTO008"),
    ],
)
def test_second_protocol_residue_is_reported(
    tmp_path: Path, path: str, content: str, rule_id: str
) -> None:
    """Shipping second-protocol residue would contradict the single-protocol delivery scope."""
    repo = _make_repo(tmp_path / "repo", {path: content})

    code, out, _ = _audit(repo)

    assert code == 1
    assert rule_id in _rule_ids(out)


def test_readme_may_name_the_unsupported_second_protocol(tmp_path: Path) -> None:
    """README has to explain the unsupported protocol, so naming it there is allowed."""
    repo = _make_repo(
        tmp_path / "repo",
        {
            "README.md": (
                f"## 不支持的第二协议\n\n本版本不支持 {_PROTO_VENDOR_NAME} Chat Completions。\n"
            ),
            "README.txt": f"说明：不支持 {_PROTO_VENDOR_NAME} 协议。\n",
        },
    )

    code, out, _ = _audit(repo, "--history")

    assert code == 0, out


def test_readme_allowance_requires_the_unsupported_context_on_the_same_line(
    tmp_path: Path,
) -> None:
    """Without the narrowing marker the allowance would license any vendor mention."""
    repo = _make_repo(
        tmp_path / "repo",
        {"README.md": f"我们计划迁移到 {_PROTO_VENDOR_NAME} 服务。\n"},
    )

    code, out, _ = _audit(repo)

    assert code == 1
    assert "PROTO008" in _rule_ids(out)


def test_readme_allowance_does_not_cover_protocol_field_names(tmp_path: Path) -> None:
    """README may name the protocol but must not carry its request or response fields."""
    repo = _make_repo(
        tmp_path / "repo",
        {"README.md": f"不支持的第二协议字段 {_PROTO_FINISH_FIELD} 未实现。\n"},
    )

    code, out, _ = _audit(repo)

    assert code == 1
    assert "PROTO005" in _rule_ids(out)


def test_public_design_documents_may_discuss_the_second_protocol(tmp_path: Path) -> None:
    """The design documents define the exclusion, so they must be able to name its fields."""
    repo = _make_repo(
        tmp_path / "repo",
        {
            "doc/项目设计方案.md": (
                f"不得出现 `{_PROTO_PACKAGE}`、`{_PROTO_CLIENT_CLASS}`、`{_PROTO_ENDPOINT}`、"
                f"`{_PROTO_FINISH_FIELD}`、`{_PROTO_STREAM_FIELD}` 或 `{_PROTO_TOKENS_FIELD}`。\n"
            )
        },
    )

    code, out, _ = _audit(repo, "--history")

    assert code == 0, out


def test_design_document_allowance_does_not_cover_a_real_import(tmp_path: Path) -> None:
    """A runnable import in a document is residue, not a discussion of the exclusion."""
    repo = _make_repo(tmp_path / "repo", {"doc/plan.md": f"import {_PROTO_PACKAGE}\n"})

    code, out, _ = _audit(repo)

    assert code == 1
    assert "PROTO001" in _rule_ids(out)


@pytest.mark.parametrize("path", ["evaluation/README.md", "doc/ref/notes.md", "src/README.md"])
def test_second_protocol_allowances_are_path_exact(tmp_path: Path, path: str) -> None:
    """A prefix match would let any nested README or document carry protocol residue."""
    repo = _make_repo(tmp_path / "repo", {path: f"不支持 {_PROTO_VENDOR_NAME}。\n"})

    code, out, _ = _audit(repo)

    assert code == 1
    assert "PROTO008" in _rule_ids(out)


def test_history_finding_is_reported_separately_from_the_working_tree(tmp_path: Path) -> None:
    """A permanent historical hit must not mask or block a clean working-tree gate."""
    repo = _make_repo(tmp_path / "repo", {"src/client.py": f'KEY = "{_FAKE_ANTHROPIC_KEY}"\n'})
    _commit(repo, {"src/client.py": "KEY = os.environ['ANTHROPIC_API_KEY']\n"}, "read key from env")

    tree_code, tree_out, _ = _audit(repo)
    history_code, history_out, _ = _audit(repo, "--history")

    assert tree_code == 0
    assert "AUDIT_SUMMARY status=clean findings=0 tree=0 history=0" in tree_out
    assert "history_scanned=false" in tree_out
    assert history_code == 2
    assert "AUDIT_SUMMARY status=history_only findings=1 tree=0 history=1" in history_out
    assert "history_scanned=true" in history_out
    assert "history CRED001 src/client.py:1@" in history_out
    assert _FAKE_ANTHROPIC_KEY not in history_out


def test_history_forbidden_path_reports_the_commit_that_introduced_it(tmp_path: Path) -> None:
    """The gate has to name the commit so a human can decide how to remediate it."""
    repo = _make_repo(tmp_path / "repo", {".superpowers/notes.md": "internal\n"})
    introduced = subprocess.run(
        ["git", "log", "--format=%h", "--abbrev=12", "-1"],
        cwd=repo,
        env=_GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    code, out, _ = _audit(repo, "--history")

    assert code == 1
    assert f"history PATH001 .superpowers/notes.md@{introduced} ::" in out


def test_forbidden_path_with_several_versions_is_reported_once(tmp_path: Path) -> None:
    """One finding per forbidden path keeps the report reviewable as the file keeps changing."""
    repo = _make_repo(tmp_path / "repo", {"tmp/scratch.txt": "first\n"})
    _commit(repo, {"tmp/scratch.txt": "second\n"}, "update scratch file")
    _commit(repo, {"tmp/scratch.txt": "third\n"}, "update scratch file again")

    code, out, _ = _audit(repo, "--history")

    assert code == 1
    assert _rule_ids(out) == ["PATH003", "PATH003"]
    assert "AUDIT_SUMMARY status=fail findings=2 tree=1 history=1" in out


def test_working_tree_finding_dominates_the_exit_code(tmp_path: Path) -> None:
    """A new regression must fail hard even when the history already has an old finding."""
    repo = _make_repo(tmp_path / "repo", {"src/client.py": f'KEY = "{_FAKE_ANTHROPIC_KEY}"\n'})

    code, out, _ = _audit(repo, "--history")

    assert code == 1
    assert "AUDIT_SUMMARY status=fail" in out
    assert "tree=1" in out


def test_findings_are_deterministic_and_sorted(tmp_path: Path) -> None:
    """Unstable ordering would make every gate run produce an unreviewable diff."""
    repo = _make_repo(
        tmp_path / "repo",
        {
            "tmp/scratch.txt": "placeholder\n",
            ".env": "PLACEHOLDER=1\n",
            "src/client.py": f'api_key = "{_FAKE_CREDENTIAL_VALUE}"\n',
            "docs/run.md": f"run /Users/{_LOCAL_USER}/projects/demo/main.py\n",
        },
    )

    first = _audit(repo, "--history")
    second = _audit(repo, "--history")

    assert first == second
    lines = [line for line in first[1].splitlines() if not line.startswith("AUDIT_SUMMARY")]
    tree_lines = [line for line in lines if line.startswith("tree ")]
    history_lines = [line for line in lines if line.startswith("history ")]
    assert lines == tree_lines + history_lines
    assert tree_lines == sorted(tree_lines)
    assert history_lines == sorted(history_lines)
    assert _rule_ids(first[1])[: len(tree_lines)] == ["CRED002", "PATH003", "PATH005", "TRACE001"]


def test_summary_line_lists_the_rules_that_fired(tmp_path: Path) -> None:
    """The summary is the machine-readable handle the release gate records."""
    repo = _make_repo(tmp_path / "repo", {"tmp/scratch.txt": "placeholder\n"})

    code, out, _ = _audit(repo, "--history")

    assert code == 1
    summary = [line for line in out.splitlines() if line.startswith("AUDIT_SUMMARY")]
    assert len(summary) == 1
    assert "findings=2 tree=1 history=1" in summary[0]
    assert "rules=PATH003" in summary[0]


def test_binary_content_does_not_break_the_scan(tmp_path: Path) -> None:
    """A binary asset must not abort the audit before the text files are scanned."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "assets").mkdir()
    (repo / "assets" / "logo.bin").write_bytes(bytes(range(256)))
    (repo / "src").mkdir()
    (repo / "src" / "client.py").write_text(f'api_key = "{_FAKE_CREDENTIAL_VALUE}"\n', "utf-8")
    _git(repo, "add", "-A", ".")
    _git(repo, "commit", "-m", "add binary asset")

    code, out, _ = _audit(repo, "--history")

    assert code == 1
    assert "CRED002" in _rule_ids(out)


def test_repo_argument_is_resolved_to_the_repository_root(tmp_path: Path) -> None:
    """Paths reported from a subdirectory would no longer match the repository-relative rules."""
    repo = _make_repo(
        tmp_path / "repo",
        {"tmp/scratch.txt": "placeholder\n", "src/app.py": "print('ok')\n"},
    )

    code, out, _ = _audit(repo / "src")

    assert code == 1
    assert "tree PATH003 tmp/scratch.txt ::" in out


def test_non_git_directory_reports_a_usage_error(tmp_path: Path) -> None:
    """Silently passing on a non-repository would turn the gate into a no-op."""
    plain = tmp_path / "plain"
    plain.mkdir()

    code, out, err = _audit(plain, "--history")

    assert code == 3
    assert "AUDIT_SUMMARY" not in out
    assert err.strip() != ""


def test_delivery_scripts_and_their_tests_pass_their_own_rules(tmp_path: Path) -> None:
    """A detector that trips its own rules cannot be used as the release gate."""
    sources = {
        "scripts/audit_public.py": _AUDIT_SCRIPT,
        "scripts/check_readme_txt.py": _REPO_ROOT / "scripts" / "check_readme_txt.py",
        "tests/unit/test_audit_public.py": Path(__file__).resolve(),
        "tests/unit/test_readme_txt.py": Path(__file__).resolve().parent / "test_readme_txt.py",
    }
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    for relative, source in sources.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    _git(repo, "add", "-A", ".")
    _git(repo, "commit", "-m", "copy the delivery audit tooling")

    code, out, _ = _audit(repo, "--history")

    assert code == 0, out


def test_audit_script_does_not_import_the_application_package() -> None:
    """The gate must run even when the package is not installed or importable."""
    source = _AUDIT_SCRIPT.read_text(encoding="utf-8")

    assert "coding_agent" not in source


def test_documented_cli_invocation_works_as_a_subprocess(tmp_path: Path) -> None:
    """The documented command is the contract the release checklist executes."""
    repo = _make_repo(tmp_path / "repo", {"src/app.py": "print('ok')\n"})

    completed = subprocess.run(
        [sys.executable, str(_AUDIT_SCRIPT), "--repo", str(repo), "--history"],
        cwd=tmp_path,
        env={**_GIT_ENV, "PYTHONPATH": ""},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "AUDIT_SUMMARY status=clean findings=0" in completed.stdout
