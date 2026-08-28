"""Offline task definitions for the real-model smoke described in section 17.4.

Nothing in this module calls a model or touches the network. Each task is a tiny workspace
tree, a prompt, one exact verification command the model is allowed to run, and the local
checks the harness runs itself with a working directory it controls. Keeping the trees here
rather than inside the live tests lets an offline test prove that every baseline fails and
every gold overlay passes before a single token is spent, and it keeps the live assertions
structural instead of dependent on model wording.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# The one command each task allows. The prompt quotes it verbatim and the generated
# command-policy-v1 file allows exactly this string in the workspace root, so anything else
# the model proposes comes back as a normal COMMAND_NOT_ALLOWED tool error it can react to.
VERIFY_COMMAND = "python3 -B -m unittest discover -s tests -t . -q"
_VERIFY_ARGS: tuple[str, ...] = ("-B", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-q")

_LOCALE_ENV_NAMES = ("LANG", "LC_ALL", "LC_CTYPE")

_TASK_TWO_TARGET = """
import sys

from mathkit.ranges import clamp

try:
    clamp(1, 5, 2)
except ValueError:
    sys.exit(0)
sys.exit(1)
"""


@dataclass(frozen=True, slots=True)
class LocalCheck:
    """One deterministic check the harness runs itself after the agent finishes."""

    name: str
    args: tuple[str, ...]
    must_pass_at_baseline: bool


@dataclass(frozen=True, slots=True)
class LiveTask:
    """One live scenario: a baseline tree, a prompt, a gold overlay and local checks."""

    task_id: str
    prompt: str
    baseline: Mapping[str, str]
    gold: Mapping[str, str]
    protected: tuple[str, ...]
    expected_files: tuple[str, ...]
    checks: tuple[LocalCheck, ...]

    @property
    def commands(self) -> tuple[tuple[str, str], ...]:
        """The exact ``(command, workspace-relative cwd)`` pairs this task authorizes."""
        return ((VERIFY_COMMAND, "."),)


def _unittest_check(*, must_pass_at_baseline: bool) -> LocalCheck:
    return LocalCheck("unittest", _VERIFY_ARGS, must_pass_at_baseline)


_TESTS_HEADER = "import unittest\n\n"

# ``unittest discover -s tests -t .`` refuses a start directory that is not importable when a
# separate top-level directory is given, so every baseline ships this package marker.
_TESTS_PACKAGE = '"""Test package."""\n'


CREATE_FILE_AND_RUN_TEST = LiveTask(
    task_id="create-file-and-run-test",
    prompt=(
        "在当前工作目录中新建 `textkit/slug.py`，实现函数 `slugify(text)`：把输入转成小写、"
        "按空白切分、丢弃空片段，再用单个 `-` 连接。\n\n"
        "现有测试 `tests/test_slug.py` 已经写好，不要修改它，也不要新建其它测试。\n\n"
        "完成后运行下面这条命令验证。它是唯一被允许的命令，必须逐字使用，工作目录为仓库根：\n\n"
        f"```\n{VERIFY_COMMAND}\n```\n\n"
        "命令退出码为 0 表示完成，然后用一句话说明你改了什么。"
    ),
    baseline={
        "textkit/__init__.py": '"""Small text helpers."""\n',
        "tests/__init__.py": _TESTS_PACKAGE,
        "tests/test_slug.py": (
            _TESTS_HEADER + "from textkit.slug import slugify\n\n\n"
            "class SlugifyTests(unittest.TestCase):\n"
            "    def test_lowercases_and_joins_words(self):\n"
            '        self.assertEqual(slugify("Hello World"), "hello-world")\n\n'
            "    def test_collapses_surrounding_whitespace(self):\n"
            '        self.assertEqual(slugify("  A   B  "), "a-b")\n'
        ),
    },
    gold={
        "textkit/slug.py": (
            '"""Turn free text into a hyphenated slug."""\n\n\n'
            "def slugify(text):\n"
            '    return "-".join(part for part in text.lower().split() if part)\n'
        ),
    },
    protected=("tests/test_slug.py",),
    expected_files=("textkit/slug.py",),
    checks=(_unittest_check(must_pass_at_baseline=False),),
)


MODIFY_FUNCTION_KEEP_REGRESSION = LiveTask(
    task_id="modify-function-keep-regression",
    prompt=(
        "`mathkit/ranges.py` 里的 `clamp(value, low, high)` 缺少参数校验。"
        "请只修改这个函数：当 `low > high` 时抛出 `ValueError`，改动不超过 5 行，"
        "其余行为保持不变。\n\n"
        "不要修改 `tests/test_ranges.py`，现有测试必须继续全部通过。\n\n"
        "完成后运行下面这条命令验证。它是唯一被允许的命令，必须逐字使用，工作目录为仓库根：\n\n"
        f"```\n{VERIFY_COMMAND}\n```\n\n"
        "命令退出码为 0 表示完成，然后用一句话说明你改了什么。"
    ),
    baseline={
        "mathkit/__init__.py": '"""Small numeric helpers."""\n',
        "mathkit/ranges.py": (
            '"""Range helpers used across the project."""\n\n\n'
            "def clamp(value, low, high):\n"
            '    """Return ``value`` limited to the inclusive ``low``..``high`` range."""\n'
            "    if value < low:\n"
            "        return low\n"
            "    if value > high:\n"
            "        return high\n"
            "    return value\n"
        ),
        "tests/__init__.py": _TESTS_PACKAGE,
        "tests/test_ranges.py": (
            _TESTS_HEADER + "from mathkit.ranges import clamp\n\n\n"
            "class ClampTests(unittest.TestCase):\n"
            "    def test_keeps_a_value_inside_the_range(self):\n"
            "        self.assertEqual(clamp(3, 1, 5), 3)\n\n"
            "    def test_raises_the_lower_bound(self):\n"
            "        self.assertEqual(clamp(-2, 1, 5), 1)\n\n"
            "    def test_lowers_to_the_upper_bound(self):\n"
            "        self.assertEqual(clamp(9, 1, 5), 5)\n"
        ),
    },
    gold={
        "mathkit/ranges.py": (
            '"""Range helpers used across the project."""\n\n\n'
            "def clamp(value, low, high):\n"
            '    """Return ``value`` limited to the inclusive ``low``..``high`` range."""\n'
            "    if low > high:\n"
            '        raise ValueError("low must not exceed high")\n'
            "    if value < low:\n"
            "        return low\n"
            "    if value > high:\n"
            "        return high\n"
            "    return value\n"
        ),
    },
    protected=("tests/test_ranges.py",),
    expected_files=(),
    checks=(
        _unittest_check(must_pass_at_baseline=True),
        LocalCheck("rejects_inverted_range", ("-B", "-c", _TASK_TWO_TARGET), False),
    ),
)


SELF_CORRECT_AFTER_TOOL_FAILURE = LiveTask(
    task_id="self-correct-after-tool-failure",
    prompt=(
        "先运行下面这条命令。它是唯一被允许的命令，必须逐字使用，工作目录为仓库根：\n\n"
        f"```\n{VERIFY_COMMAND}\n```\n\n"
        "这条命令现在会失败。请阅读失败输出，修复 `parsekit/totals.py`，"
        "让它忽略空行和只含空白的行。\n\n"
        "不要修改 `tests/test_totals.py`。修复后重新运行同一条命令，直到退出码为 0，"
        "然后用一句话说明失败原因和你的修复。"
    ),
    baseline={
        "parsekit/__init__.py": '"""Small parsing helpers."""\n',
        "parsekit/totals.py": (
            '"""Sum the integers in a sequence of text lines."""\n\n\n'
            "def total(lines):\n"
            "    return sum(int(line) for line in lines)\n"
        ),
        "tests/__init__.py": _TESTS_PACKAGE,
        "tests/test_totals.py": (
            _TESTS_HEADER + "from parsekit.totals import total\n\n\n"
            "class TotalTests(unittest.TestCase):\n"
            "    def test_sums_plain_numbers(self):\n"
            '        self.assertEqual(total(["1", "2", "3"]), 6)\n\n'
            "    def test_ignores_blank_and_whitespace_lines(self):\n"
            '        self.assertEqual(total(["1", "", "   ", "4"]), 5)\n'
        ),
    },
    gold={
        "parsekit/totals.py": (
            '"""Sum the integers in a sequence of text lines."""\n\n\n'
            "def total(lines):\n"
            "    return sum(int(line) for line in lines if line.strip())\n"
        ),
    },
    protected=("tests/test_totals.py",),
    expected_files=(),
    checks=(_unittest_check(must_pass_at_baseline=False),),
)


LIVE_TASKS: tuple[LiveTask, ...] = (
    CREATE_FILE_AND_RUN_TEST,
    MODIFY_FUNCTION_KEEP_REGRESSION,
    SELF_CORRECT_AFTER_TOOL_FAILURE,
)


def materialize(files: Mapping[str, str], workspace: Path) -> Path:
    """Write ``files`` into ``workspace``, creating parent directories as needed."""
    workspace.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return workspace


def digests(files: Sequence[str], workspace: Path) -> dict[str, str | None]:
    """Return a sha256 per path, or ``None`` when the path is missing."""
    values: dict[str, str | None] = {}
    for relative in files:
        target = workspace / relative
        values[relative] = (
            hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        )
    return values


def declared_digests(files: Mapping[str, str], paths: Sequence[str]) -> dict[str, str | None]:
    """Return the sha256 of each declared path's content without touching the disk."""
    return {
        path: hashlib.sha256(files[path].encode("utf-8")).hexdigest() if path in files else None
        for path in paths
    }


def run_local_check(
    check: LocalCheck,
    workspace: Path,
    *,
    home: Path,
    timeout_seconds: int = 120,
) -> int:
    """Run one check with the harness's own interpreter and a minimal environment.

    The interpreter is ``sys.executable`` rather than whatever the agent used, and the
    environment carries no credential, so the model cannot influence its own verification.
    """
    home.mkdir(parents=True, exist_ok=True)
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in _LOCALE_ENV_NAMES:
        if name in os.environ:
            environment[name] = os.environ[name]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and in-repo check argv
            [sys.executable, *check.args],
            cwd=str(workspace),
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return -1
    return completed.returncode


__all__ = [
    "CREATE_FILE_AND_RUN_TEST",
    "LIVE_TASKS",
    "MODIFY_FUNCTION_KEEP_REGRESSION",
    "SELF_CORRECT_AFTER_TOOL_FAILURE",
    "VERIFY_COMMAND",
    "LiveTask",
    "LocalCheck",
    "declared_digests",
    "digests",
    "materialize",
    "run_local_check",
]
