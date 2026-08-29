from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from coding_agent import cli
from coding_agent.config import ConfigurationError
from coding_agent.context import Compactor, ContextBuilder
from coding_agent.core.models import (
    AssistantTurn,
    ModelStopReason,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolResult,
    ToolUsePart,
    Usage,
)
from coding_agent.main import RuntimeDependencies, load_command_policy
from coding_agent.model import ModelMessage
from coding_agent.model.protocol import ModelAPIError
from coding_agent.runtime.approval import ApprovalGate
from coding_agent.runtime.publisher import EventPublisher
from coding_agent.storage.sqlite import SQLiteStore
from coding_agent.tools.registry import ToolRegistry
from tests.fakes.model import ScriptedModel


def _final_turn(text: str = "done") -> AssistantTurn:
    return AssistantTurn(
        "turn-final",
        (TextPart(text),),
        ModelStopReason.END_TURN,
        Usage(12, 3),
    )


def _run_argv(paths: dict[str, Path]) -> list[str]:
    return [
        "run",
        "--config",
        str(paths["config"]),
        "--workspace",
        str(paths["workspace"]),
        "--data-dir",
        str(paths["data_dir"]),
        "--prompt-file",
        str(paths["prompt"]),
        "--yes",
        "--ack-unsafe-auto-approve",
        "--command-policy",
        str(paths["policy"]),
        "--report-out",
        str(paths["report"]),
    ]


def _task_files(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "config": tmp_path / "config.toml",
        "workspace": tmp_path / "workspace",
        "data_dir": tmp_path / "data",
        "prompt": tmp_path / "prompt.txt",
        "policy": tmp_path / "command-policy.json",
        "report": tmp_path / "report.json",
    }
    paths["workspace"].mkdir()
    paths["data_dir"].mkdir()
    paths["config"].write_text("", encoding="utf-8")
    paths["prompt"].write_text("finish the task", encoding="utf-8")
    paths["policy"].write_text(
        json.dumps(
            {
                "schema_version": "command-policy-v1",
                "allowed": [{"command": "pwd", "cwd": "."}],
            }
        ),
        encoding="utf-8",
    )
    return paths


def _dependencies(
    data_dir: Path,
    model: ScriptedModel | None = None,
) -> RuntimeDependencies:
    store = SQLiteStore(data_dir / "state.db")
    store.initialize()
    model = model or ScriptedModel([_final_turn()])
    return RuntimeDependencies(
        store=store,
        model=model,
        context_builder=ContextBuilder(),
        compactor=Compactor(ScriptedModel([]), store, model="scripted-compactor"),
        tool_registry=ToolRegistry(),
        approval_gate=ApprovalGate(auto_approve=True),
        clock=lambda: 0.0,
        sleeper=_no_sleep,
        event_publisher=EventPublisher(),
    )


async def _no_sleep(_: float) -> None:
    return None


def test_headless_run_uses_injected_runtime_and_writes_versioned_report(tmp_path: Path) -> None:
    """A separate headless loop could bypass the durable runtime or emit an unusable report."""
    paths = _task_files(tmp_path)
    dependencies = _dependencies(paths["data_dir"])

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(paths["config"]),
            "--workspace",
            str(paths["workspace"]),
            "--data-dir",
            str(paths["data_dir"]),
            "--prompt-file",
            str(paths["prompt"]),
            "--yes",
            "--ack-unsafe-auto-approve",
            "--command-policy",
            str(paths["policy"]),
            "--report-out",
            str(paths["report"]),
        ],
        dependencies=dependencies,
    )

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["schema_version"] == "run-report-v1"
    assert report["state"] == "COMPLETED"
    assert report["stop_reason"] == "COMPLETED"
    assert report["error_kind"] is None
    assert set(report) == {
        "schema_version",
        "run_id",
        "session_id",
        "state",
        "stop_reason",
        "error_kind",
        "started_at",
        "finished_at",
        "tool_schema_hash",
        "model",
        "tools",
        "compaction",
        "durations",
        "model_identity",
        "final_assistant_text",
    }
    assert len(report["tool_schema_hash"]) == 64
    assert report["model"]["main"]["requests"] == 1
    assert report["model"]["main"]["attempts"] == 1
    assert report["model"]["main"]["usage_coverage"] == 1.0
    assert report["model"]["compaction"]["requests"] == 0
    assert report["tools"]["proposed"] == 0
    assert report["compaction"] == {
        "count": 0,
        "requests": 0,
        "above_target": False,
        "estimator_id": None,
        "input_tokens_before": None,
        "input_tokens_after": None,
        "estimate_error": {
            "estimated_summary_tokens": None,
            "provider_summary_output_tokens": None,
            "estimated_minus_provider_tokens": None,
        },
    }
    assert report["durations"]["agent_monotonic_ms"] == 0
    assert report["durations"]["retry_wait_monotonic_ms"] == 0
    assert report["final_assistant_text"] == "done"


def test_report_records_the_model_identity_that_served_the_requests(tmp_path: Path) -> None:
    """Numbers nobody can attribute to a model are not a publishable measurement."""
    paths = _task_files(tmp_path)
    paths["config"].write_text(
        '[model]\nmodel = "claude-test-model-2026"\nmax_output_tokens = 4096\n',
        encoding="utf-8",
    )
    dependencies = _dependencies(paths["data_dir"])

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(paths["config"]),
            "--workspace",
            str(paths["workspace"]),
            "--data-dir",
            str(paths["data_dir"]),
            "--prompt-file",
            str(paths["prompt"]),
            "--yes",
            "--ack-unsafe-auto-approve",
            "--command-policy",
            str(paths["policy"]),
            "--report-out",
            str(paths["report"]),
        ],
        dependencies=dependencies,
    )

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["model_identity"] == {
        "name": "claude-test-model-2026",
        "context_window": 64000,
        "max_output_tokens": 4096,
        "stream": True,
    }
    assert "ANTHROPIC_API_KEY" not in json.dumps(report)
    assert "api.anthropic.com" not in json.dumps(report)


def test_report_keeps_missing_provider_usage_components_null(tmp_path: Path) -> None:
    """Filling a missing cache-token component with zero would falsify cost reporting."""
    paths = _task_files(tmp_path)
    dependencies = _dependencies(paths["data_dir"])

    cli.main(
        [
            "run",
            "--config",
            str(paths["config"]),
            "--workspace",
            str(paths["workspace"]),
            "--data-dir",
            str(paths["data_dir"]),
            "--prompt-file",
            str(paths["prompt"]),
            "--yes",
            "--ack-unsafe-auto-approve",
            "--command-policy",
            str(paths["policy"]),
            "--report-out",
            str(paths["report"]),
        ],
        dependencies=dependencies,
    )

    usage = json.loads(paths["report"].read_text(encoding="utf-8"))["model"]["main"]["usage"]
    assert usage == {
        "input_tokens": 12,
        "output_tokens": 3,
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
    }


def test_report_projects_tool_statistics_with_hashed_arguments(tmp_path: Path) -> None:
    """Exporting raw tool arguments would publish workspace content from every run."""
    paths = _task_files(tmp_path)
    model = ScriptedModel(
        [
            AssistantTurn(
                "turn-command",
                (ToolUsePart(ToolCall("call-pwd", "run_command", {"command": "pwd", "cwd": "."})),),
                ModelStopReason.TOOL_USE,
                Usage(10, 4),
            ),
            _final_turn(),
        ]
    )
    dependencies = _dependencies(paths["data_dir"], model)

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(paths["config"]),
            "--workspace",
            str(paths["workspace"]),
            "--data-dir",
            str(paths["data_dir"]),
            "--prompt-file",
            str(paths["prompt"]),
            "--yes",
            "--ack-unsafe-auto-approve",
            "--command-policy",
            str(paths["policy"]),
            "--report-out",
            str(paths["report"]),
        ],
        dependencies=dependencies,
    )

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    expected_hash = hashlib.sha256(
        json.dumps({"cwd": ".", "command": "pwd"}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert exit_code == 0
    assert report["tools"]["proposed"] == 1
    assert report["tools"]["executed"] == 1
    assert report["tools"]["succeeded"] == 1
    assert report["tools"]["failed"] == 0
    assert report["tools"]["duplicate_calls"] == 0
    assert report["tools"]["by_name"]["run_command"] == {
        "proposed": 1,
        "succeeded": 1,
        "failed": 0,
    }
    assert [call["args_hash"] for call in report["tools"]["calls"]] == [expected_hash]
    assert report["tools"]["output_bytes"] > 0
    with dependencies.store.connection() as connection:
        recorded_duration = connection.execute(
            "SELECT duration_ms FROM tool_executions"
        ).fetchone()[0]
    assert recorded_duration is not None
    assert [call["duration_ms"] for call in report["tools"]["calls"]] == [recorded_duration]
    assert report["durations"]["tool_execution_monotonic_ms"] == recorded_duration
    assert report["model"]["main"]["requests"] == 2
    assert str(paths["workspace"]) not in json.dumps(report)


def test_run_reads_the_default_config_from_the_startup_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """README §3 promises a default config file, so requiring the flag contradicts the delivery."""
    paths = _task_files(tmp_path)
    paths["config"].write_text(
        '[model]\nmodel = "claude-default-directory-2026"\n', encoding="utf-8"
    )
    dependencies = _dependencies(paths["data_dir"])
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(
        [
            "run",
            "--workspace",
            str(paths["workspace"]),
            "--data-dir",
            str(paths["data_dir"]),
            "--prompt-file",
            str(paths["prompt"]),
            "--yes",
            "--ack-unsafe-auto-approve",
            "--command-policy",
            str(paths["policy"]),
            "--report-out",
            str(paths["report"]),
        ],
        dependencies=dependencies,
    )

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["model_identity"]["name"] == "claude-default-directory-2026"


def test_explicit_config_path_wins_over_the_startup_directory_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A default that silently overrode an explicit path would run an unrequested configuration."""
    paths = _task_files(tmp_path)
    paths["config"].write_text(
        '[model]\nmodel = "claude-default-directory-2026"\n', encoding="utf-8"
    )
    explicit = tmp_path / "explicit-config.toml"
    explicit.write_text('[model]\nmodel = "claude-explicit-config-2026"\n', encoding="utf-8")
    dependencies = _dependencies(paths["data_dir"])
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(explicit),
            "--workspace",
            str(paths["workspace"]),
            "--data-dir",
            str(paths["data_dir"]),
            "--prompt-file",
            str(paths["prompt"]),
            "--yes",
            "--ack-unsafe-auto-approve",
            "--command-policy",
            str(paths["policy"]),
            "--report-out",
            str(paths["report"]),
        ],
        dependencies=dependencies,
    )

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["model_identity"]["name"] == "claude-explicit-config-2026"


@pytest.mark.parametrize("command", ["serve", "run"])
def test_missing_configuration_file_is_a_named_configuration_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    """Falling back to built-in defaults, or a bare traceback, would hide an unusable setup."""
    paths = _task_files(tmp_path)
    dependencies = _dependencies(paths["data_dir"])
    startup = tmp_path / "startup"
    startup.mkdir()
    monkeypatch.chdir(startup)
    argv = [command]
    if command == "run":
        argv += [
            "--workspace",
            str(paths["workspace"]),
            "--data-dir",
            str(paths["data_dir"]),
            "--prompt-file",
            str(paths["prompt"]),
            "--report-out",
            str(paths["report"]),
        ]

    exit_code = cli.main(argv, dependencies=dependencies)

    error = capsys.readouterr().err
    assert exit_code == 2
    assert error.startswith("CONFIG_ERROR: ")
    assert "config.toml" in error
    assert "Traceback" not in error
    assert dependencies.store.list_sessions() == []
    assert not paths["report"].exists()


def test_config_defaults_to_the_startup_directory_for_both_commands() -> None:
    """A default on only one subcommand would make the documented flag half true."""
    parser = cli.build_parser()

    serve = parser.parse_args(["serve"])
    run = parser.parse_args(
        [
            "run",
            "--workspace",
            "workspace",
            "--data-dir",
            "data",
            "--prompt-file",
            "prompt.txt",
            "--report-out",
            "report.json",
        ]
    )

    assert serve.config == Path("config.toml")
    assert run.config == Path("config.toml")


@pytest.mark.parametrize(
    "omitted",
    ["--ack-unsafe-auto-approve", "--command-policy"],
)
def test_auto_approve_preflight_fails_before_session_creation(
    tmp_path: Path,
    omitted: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unsafe auto-approval without both gates must not leave any durable run artifacts."""
    paths = _task_files(tmp_path)
    dependencies = _dependencies(paths["data_dir"])
    argv = [
        "run",
        "--config",
        str(paths["config"]),
        "--workspace",
        str(paths["workspace"]),
        "--data-dir",
        str(paths["data_dir"]),
        "--prompt-file",
        str(paths["prompt"]),
        "--yes",
        "--ack-unsafe-auto-approve",
        "--command-policy",
        str(paths["policy"]),
        "--report-out",
        str(paths["report"]),
    ]
    option_index = argv.index(omitted)
    del argv[option_index : option_index + (2 if omitted == "--command-policy" else 1)]

    exit_code = cli.main(argv, dependencies=dependencies)

    assert exit_code == 2
    assert "CONFIG_ERROR" in capsys.readouterr().err
    assert dependencies.store.list_sessions() == []
    assert not paths["report"].exists()


@pytest.mark.parametrize(
    "policy_value",
    [
        {"schema_version": "command-policy-v1", "allowed": []},
        {
            "schema_version": "command-policy-v1",
            "allowed": [{"command": "   ", "cwd": "."}],
        },
        {
            "schema_version": "command-policy-v1",
            "allowed": [{"command": "pwd", "cwd": "../outside"}],
        },
        {
            "schema_version": "command-policy-v1",
            "allowed": [{"command_prefix": "py", "cwd": "."}],
        },
    ],
)
def test_auto_approve_rejects_unsafe_policy_before_session_creation(
    tmp_path: Path,
    policy_value: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty, escaping, or prefix policies must never enable unattended effects."""
    paths = _task_files(tmp_path)
    paths["policy"].write_text(json.dumps(policy_value), encoding="utf-8")
    dependencies = _dependencies(paths["data_dir"])

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(paths["config"]),
            "--workspace",
            str(paths["workspace"]),
            "--data-dir",
            str(paths["data_dir"]),
            "--prompt-file",
            str(paths["prompt"]),
            "--yes",
            "--ack-unsafe-auto-approve",
            "--command-policy",
            str(paths["policy"]),
            "--report-out",
            str(paths["report"]),
        ],
        dependencies=dependencies,
    )

    assert exit_code == 2
    assert "CONFIG_ERROR" in capsys.readouterr().err
    assert dependencies.store.list_sessions() == []


def test_command_policy_rejects_absolute_cwd_even_inside_workspace(tmp_path: Path) -> None:
    """Accepting machine-specific absolute cwd values would make an evaluation non-portable."""
    paths = _task_files(tmp_path)
    paths["policy"].write_text(
        json.dumps(
            {
                "schema_version": "command-policy-v1",
                "allowed": [{"command": "pwd", "cwd": str(paths["workspace"])}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="relative"):
        load_command_policy(paths["policy"], paths["workspace"])


def test_loaded_command_policy_matches_only_exact_command_and_relative_cwd(
    tmp_path: Path,
) -> None:
    """Treating policy strings as prefixes or globs would authorize unlisted commands."""
    paths = _task_files(tmp_path)
    (paths["workspace"] / "nested").mkdir()
    paths["policy"].write_text(
        json.dumps(
            {
                "schema_version": "command-policy-v1",
                "allowed": [{"command": "pytest tests/unit", "cwd": "nested"}],
            }
        ),
        encoding="utf-8",
    )

    policy = load_command_policy(paths["policy"], paths["workspace"])

    assert policy.allows("pytest tests/unit", Path("nested")) is True
    assert policy.allows("pytest tests/unit -q", Path("nested")) is False
    assert policy.allows("pytest tests/unit", Path(".")) is False


def test_injected_runtime_still_enforces_cli_command_policy(tmp_path: Path) -> None:
    """Injecting a model for offline runs must not bypass the evaluator's effect allowlist."""
    paths = _task_files(tmp_path)
    forbidden = paths["workspace"] / "forbidden.txt"
    model = ScriptedModel(
        [
            AssistantTurn(
                "turn-command",
                (
                    ToolUsePart(
                        ToolCall(
                            "call-command",
                            "run_command",
                            {"command": "printf forbidden > forbidden.txt", "cwd": "."},
                        )
                    ),
                ),
                ModelStopReason.TOOL_USE,
                Usage(10, 4),
            ),
            _final_turn(),
        ]
    )
    dependencies = _dependencies(paths["data_dir"], model)

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(paths["config"]),
            "--workspace",
            str(paths["workspace"]),
            "--data-dir",
            str(paths["data_dir"]),
            "--prompt-file",
            str(paths["prompt"]),
            "--yes",
            "--ack-unsafe-auto-approve",
            "--command-policy",
            str(paths["policy"]),
            "--report-out",
            str(paths["report"]),
        ],
        dependencies=dependencies,
    )

    returned_results = [
        part
        for message in model.requests[1].messages
        if isinstance(message, ModelMessage)
        for part in message.parts
        if isinstance(part, ToolResult)
    ]
    assert exit_code == 0
    assert forbidden.exists() is False
    assert returned_results[0].error is not None
    assert returned_results[0].error.code == "COMMAND_NOT_ALLOWED"


def test_parser_has_serve_and_run_without_a_scripted_model_switch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A product flag selecting the deterministic test model could expose a test backdoor."""
    parser = cli.build_parser()

    serve = parser.parse_args(["serve", "--config", "config.toml", "--port", "8123"])
    assert serve.command == "serve"
    assert serve.port == 8123
    with pytest.raises(SystemExit) as raised:
        parser.parse_args(
            [
                "run",
                "--config",
                "config.toml",
                "--workspace",
                "workspace",
                "--data-dir",
                "data",
                "--prompt-file",
                "prompt.txt",
                "--report-out",
                "report.json",
                "--scripted-model",
            ]
        )

    error = capsys.readouterr().err
    assert raised.value.code == 2
    assert "unrecognized arguments: --scripted-model" in error
    assert "the following arguments are required" not in error


def test_serve_uses_the_injected_runtime_and_fixed_loopback_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leaving the frozen serve command unwired would make the browser API unreachable."""
    paths = _task_files(tmp_path)
    dependencies = _dependencies(paths["data_dir"])
    captured: dict[str, object] = {}

    def fake_run(app, *, host: str, port: int, log_level: str) -> None:
        captured.update(app=app, host=host, port=port, log_level=log_level)

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr("webbrowser.open", lambda url: captured.update(browser_url=url))

    exit_code = cli.main(
        [
            "serve",
            "--config",
            str(paths["config"]),
            "--workspace",
            str(paths["workspace"]),
            "--data-dir",
            str(paths["data_dir"]),
            "--port",
            "8123",
        ],
        dependencies=dependencies,
    )

    assert exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8123
    assert captured["log_level"] == "info"
    assert captured["browser_url"] == "http://127.0.0.1:8123"
    assert captured["app"].state.api_dependencies.store is dependencies.store
    sessions = dependencies.store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].workspace_realpath == str(paths["workspace"].resolve())


def test_report_exports_the_last_committed_assistant_text_of_the_run(tmp_path: Path) -> None:
    """The judge's communication axis scores the real final message, not a fixture-only field."""
    paths = _task_files(tmp_path)
    model = ScriptedModel(
        [
            AssistantTurn(
                "turn-text-then-tool",
                (
                    TextPart("I will inspect the workspace first."),
                    ThinkingPart("plan the edit"),
                    ToolUsePart(
                        ToolCall("call-pwd", "run_command", {"command": "pwd", "cwd": "."})
                    ),
                ),
                ModelStopReason.TOOL_USE,
                Usage(10, 4),
            ),
            _final_turn("Created helper.py and verified it with the existing suite."),
        ]
    )
    dependencies = _dependencies(paths["data_dir"], model)

    exit_code = cli.main(_run_argv(paths), dependencies=dependencies)

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["final_assistant_text"] == (
        "Created helper.py and verified it with the existing suite."
    )


def test_report_leaves_final_assistant_text_null_without_a_committed_assistant_message(
    tmp_path: Path,
) -> None:
    """A run failing before its first assistant message must export null, not a guess."""
    paths = _task_files(tmp_path)
    model = ScriptedModel([ModelAPIError(401, "authentication_error", None, False)])
    dependencies = _dependencies(paths["data_dir"], model)

    exit_code = cli.main(_run_argv(paths), dependencies=dependencies)

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert exit_code == 1
    assert report["state"] == "FAILED"
    assert report["final_assistant_text"] is None


def test_report_bounds_a_verbose_final_assistant_message(tmp_path: Path) -> None:
    """A verbose final message must not bloat every run report without notice."""
    paths = _task_files(tmp_path)
    verbose = "x" * 20_000
    dependencies = _dependencies(paths["data_dir"], ScriptedModel([_final_turn(verbose)]))

    cli.main(_run_argv(paths), dependencies=dependencies)

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    exported = report["final_assistant_text"]
    assert isinstance(exported, str)
    assert len(exported) <= 8_200
    assert "truncated" in exported
