from __future__ import annotations

import functools
import importlib.util
import io
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECK_SCRIPT = _REPO_ROOT / "scripts" / "check_readme_txt.py"

# Assembled from fragments so that this file, which scripts/audit_public.py also scans, never
# contains a literal credential-shaped token of its own.
_FAKE_ANTHROPIC_KEY = "sk-ant-" + "fake0" * 6

_BASE_BODY = (
    "My Coding Agent 是一个本地编程 Agent。\n"
    "公开仓库：https://github.com/hanhaiqingchuan/my-coding-agent\n"
    "最短运行方法：\n"
    "1. make install\n"
    "2. export ANTHROPIC_API_KEY=<你的密钥>\n"
    "3. make start，然后在浏览器打开本地服务地址\n"
    "特色功能：自研 Anthropic Messages 流式解析、Agent Loop 与终止判定、"
    "上下文预算与压缩、三个本地工具的审批与执行、SQLite 持久化与断线恢复、"
    "网页工作台与固定审批坞、离线评测。\n"
)


@functools.lru_cache(maxsize=1)
def _check_module() -> ModuleType:
    if str(_CHECK_SCRIPT.parent) not in sys.path:
        sys.path.insert(0, str(_CHECK_SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("check_readme_txt_under_test", _CHECK_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _body(code_points: int | None = None, *, base: str = _BASE_BODY) -> str:
    body = base.strip()
    if code_points is None:
        return body
    assert code_points > len(body) + 1
    return body + "\n" + "补" * (code_points - len(body) - 1)


def _write(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "README.txt"
    target.write_text(body + "\n", encoding="utf-8")
    return target


def _check(path: Path, *extra: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = _check_module().main([str(path), *extra])
    return code, stdout.getvalue(), stderr.getvalue()


def test_complete_readme_passes_and_reports_its_code_point_count(tmp_path: Path) -> None:
    """The deliverable check has to accept a README that satisfies every required field."""
    body = _body()

    code, out, _ = _check(_write(tmp_path, body))

    assert code == 0
    assert f"README_TXT_SUMMARY status=clean violations=0 code_points={len(body)}" in out
    assert "limit=1000" in out


def test_body_of_exactly_one_thousand_code_points_passes(tmp_path: Path) -> None:
    """The limit is 1000 Unicode code points, so a full-length Chinese body must pass."""
    body = _body(1000)
    assert len(body) == 1000
    assert len(body.encode("utf-8")) > 1000

    code, out, _ = _check(_write(tmp_path, body))

    assert code == 0, out
    assert "code_points=1000" in out


def test_body_of_one_thousand_and_one_code_points_fails(tmp_path: Path) -> None:
    """Counting bytes or words instead of code points would let an over-long README ship."""
    body = _body(1001)
    assert len(body) == 1001

    code, out, _ = _check(_write(tmp_path, body))

    assert code == 1
    assert "field=body_length" in out
    assert "code_points=1001" in out
    assert "status=fail" in out


def test_missing_repository_url_fails(tmp_path: Path) -> None:
    """The public repository URL is the only way a reviewer can reach the delivery."""
    body = _body(
        base=_BASE_BODY.replace("https://github.com/hanhaiqingchuan/my-coding-agent", "见仓库")
    )

    code, out, _ = _check(_write(tmp_path, body))

    assert code == 1
    assert "field=repository_url" in out


def test_expected_repository_url_must_match_when_pinned(tmp_path: Path) -> None:
    """The release gate pins the exact URL so a stale or wrong repository is caught."""
    code, out, _ = _check(
        _write(tmp_path, _body()),
        "--expect-url",
        "https://github.com/example/other-repository",
    )

    assert code == 1
    assert "field=repository_url" in out


@pytest.mark.parametrize("removed", ["make install", "ANTHROPIC_API_KEY"])
def test_missing_run_procedure_fails(tmp_path: Path, removed: str) -> None:
    """Without a command and the required environment variable the README is not runnable."""
    base = _BASE_BODY.replace(removed, "略")
    base = base.replace("make start", "略") if removed == "make install" else base

    code, out, _ = _check(_write(tmp_path, _body(base=base)))

    assert code == 1
    assert "field=run_procedure" in out


def test_missing_feature_summary_fails(tmp_path: Path) -> None:
    """A README without a feature summary does not describe the delivery at all."""
    base = (
        "My Coding Agent。\n"
        "公开仓库：https://github.com/hanhaiqingchuan/my-coding-agent\n"
        "运行：make install 后设置 ANTHROPIC_API_KEY，再执行 make start。\n"
    )

    code, out, _ = _check(_write(tmp_path, _body(base=base)))

    assert code == 1
    assert "field=feature_summary" in out


def test_credential_pattern_is_rejected_without_echoing_it(tmp_path: Path) -> None:
    """A README is a public artifact, so a pasted key must fail the check, not be printed."""
    base = _BASE_BODY.replace("<你的密钥>", _FAKE_ANTHROPIC_KEY)

    code, out, _ = _check(_write(tmp_path, _body(base=base)))

    assert code == 1
    assert "field=credentials" in out
    assert "CRED001" in out
    assert _FAKE_ANTHROPIC_KEY not in out


def test_every_violation_is_reported_in_one_run(tmp_path: Path) -> None:
    """Reporting one field at a time would force the author through repeated gate runs."""
    body = _body(base="这是一个不完整的说明文件。\n")

    code, out, _ = _check(_write(tmp_path, body))

    assert code == 1
    fields = {line.split("field=")[1].split()[0] for line in out.splitlines() if "field=" in line}
    assert fields == {"repository_url", "run_procedure", "feature_summary"}


def test_missing_file_reports_a_usage_error(tmp_path: Path) -> None:
    """A missing deliverable must not be mistaken for a passing check."""
    code, out, err = _check(tmp_path / "absent.txt")

    assert code == 2
    assert "README_TXT_SUMMARY" not in out
    assert err.strip() != ""


def test_check_script_does_not_import_the_application_package() -> None:
    """The deliverable check must run without the package being installed."""
    source = _CHECK_SCRIPT.read_text(encoding="utf-8")

    assert "coding_agent" not in source


def test_documented_cli_invocation_works_as_a_subprocess(tmp_path: Path) -> None:
    """The documented command is the contract the release checklist executes."""
    target = _write(tmp_path, _body())

    completed = subprocess.run(
        [sys.executable, str(_CHECK_SCRIPT), str(target)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "README_TXT_SUMMARY status=clean" in completed.stdout
