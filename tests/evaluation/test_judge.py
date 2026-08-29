"""Offline tests for the LLM judge: prompt construction, parsing, records, and the
campaign hook. Every model interaction is driven by a fake gateway or a monkeypatched
Anthropic SDK client; no test touches the network.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from coding_agent.config import ModelSettings
from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.models import AssistantTurn, ModelStopReason, TextPart, Usage
from coding_agent.evaluation import cli as evaluation_cli
from coding_agent.evaluation.judge import (
    JUDGE_ERROR,
    JUDGEMENT_SCHEMA_VERSION,
    PROMPT_VERSION,
    Judgement,
    build_transcript_excerpt,
    judge_prompt,
    judge_run,
    parse_judgement,
    redact_text,
    write_judgement,
)
from coding_agent.evaluation.manifest import validate_manifest
from coding_agent.evaluation.report import (
    OracleFacts,
    RunResult,
    run_document,
    score_result,
    summarize,
    summarize_campaign,
)
from coding_agent.evaluation.runner import AgentProcessResult, build_judge_hook, run_campaign
from coding_agent.model.protocol import ModelRequest, TextDelta
from tests.evaluation.conftest import task_table, write_manifest

SCHEMAS = Path(__file__).resolve().parents[2] / "evaluation" / "schemas"

VALID_RESPONSE = json.dumps(
    {
        "task_completion": 4,
        "process_quality": 5,
        "communication": 3,
        "rationale": "The goal was met with tidy tool use.",
    }
)


class FakeGateway:
    """A ModelGateway stand-in that replies with scripted judge responses."""

    def __init__(self, responses: list[str] | None = None, error: Exception | None = None) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.requests: list[ModelRequest] = []

    async def complete(
        self,
        request: ModelRequest,
        on_text_delta: Any,
        cancellation: CancellationToken,
        *,
        on_thinking_delta: Any = None,
        on_thinking_block_closed: Any = None,
    ) -> AssistantTurn:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        text = self.responses.pop(0) if self.responses else "{}"
        on_text_delta(TextDelta(index=0, text=text))
        return AssistantTurn(
            id=f"judge-{len(self.requests)}",
            parts=(TextPart(text),),
            stop_reason=ModelStopReason.END_TURN,
            usage=Usage(),
        )


# --- parsing ---------------------------------------------------------------


def test_parse_judgement_accepts_the_fixed_json() -> None:
    """The judge contract is one JSON object with three 1-5 scores and a rationale."""
    judgement = parse_judgement(VALID_RESPONSE, judge_model="claude-judge-2026")

    assert judgement.ok is True
    assert judgement.scores == {
        "task_completion": 4,
        "process_quality": 5,
        "communication": 3,
    }
    assert judgement.rationale.startswith("The goal was met")
    assert judgement.judge_model == "claude-judge-2026"
    assert judgement.prompt_version == PROMPT_VERSION
    assert judgement.schema_version == JUDGEMENT_SCHEMA_VERSION


def test_parse_judgement_accepts_a_fenced_code_block() -> None:
    """Models frequently wrap JSON in a fence; one fence block is still parseable."""
    fenced = f"Here is my judgement:\n```json\n{VALID_RESPONSE}\n```"

    judgement = parse_judgement(fenced, judge_model="claude-judge-2026")

    assert judgement.ok is True
    assert judgement.scores["task_completion"] == 4


@pytest.mark.parametrize(
    "response",
    [
        "not json at all",
        "{}",
        json.dumps({"task_completion": 4, "process_quality": 5, "rationale": "x"}),
        json.dumps(
            {
                "task_completion": 4,
                "process_quality": 5,
                "communication": 3,
                "rationale": "x",
                "confidence": 0.9,
            }
        ),
        json.dumps(
            {
                "task_completion": 0,
                "process_quality": 5,
                "communication": 3,
                "rationale": "x",
            }
        ),
        json.dumps(
            {
                "task_completion": 6,
                "process_quality": 5,
                "communication": 3,
                "rationale": "x",
            }
        ),
        json.dumps(
            {
                "task_completion": "4",
                "process_quality": 5,
                "communication": 3,
                "rationale": "x",
            }
        ),
        json.dumps(
            {
                "task_completion": 4,
                "process_quality": 5,
                "communication": 3,
                "rationale": "   ",
            }
        ),
    ],
)
def test_parse_judgement_rejects_every_malformed_response(response: str) -> None:
    """Anything outside the fixed contract is malformed, whatever the reason."""
    from coding_agent.evaluation.judge import JudgeParseError

    with pytest.raises(JudgeParseError):
        parse_judgement(response, judge_model="claude-judge-2026")


# --- the judge request -----------------------------------------------------


@pytest.mark.asyncio
async def test_judge_run_parses_a_valid_response_with_one_request() -> None:
    """The judge asks exactly once, with no tools, and scores the response."""
    gateway = FakeGateway(responses=[VALID_RESPONSE])
    run = _run_document()
    excerpt = build_transcript_excerpt(run, "Created the helper and ran the suite.")

    judgement = await judge_run(
        run, excerpt, ModelSettings(model="claude-judge-2026"), gateway=gateway
    )

    assert judgement.ok is True
    assert judgement.judge_model == "claude-judge-2026"
    assert judgement.error is None
    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert request.tools == ()
    assert request.max_tokens <= 1024
    assert request.system
    assert len(request.messages) == 1


@pytest.mark.asyncio
async def test_judge_run_retries_malformed_json_once_then_records_judge_error() -> None:
    """A second malformed answer becomes a recorded judge_error, never an exception."""
    gateway = FakeGateway(responses=["garbage", "still not json"])
    run = _run_document()

    judgement = await judge_run(
        run,
        build_transcript_excerpt(run),
        ModelSettings(model="claude-judge-2026"),
        gateway=gateway,
    )

    assert judgement.ok is False
    assert judgement.error == JUDGE_ERROR
    assert judgement.error_detail is not None and "malformed" in judgement.error_detail
    assert judgement.scores == {}
    assert judgement.rationale == ""
    assert judgement.judge_model == "claude-judge-2026"
    assert judgement.prompt_version == PROMPT_VERSION
    assert len(gateway.requests) == 2


@pytest.mark.asyncio
async def test_judge_run_recovers_when_the_retry_answers_correctly() -> None:
    """One retry is enough when the second answer is the fixed JSON."""
    gateway = FakeGateway(responses=["garbage", VALID_RESPONSE])
    run = _run_document()

    judgement = await judge_run(
        run,
        build_transcript_excerpt(run),
        ModelSettings(model="claude-judge-2026"),
        gateway=gateway,
    )

    assert judgement.ok is True
    assert judgement.scores["task_completion"] == 4
    assert len(gateway.requests) == 2


@pytest.mark.asyncio
async def test_judge_run_records_a_model_failure_without_retrying_or_raising() -> None:
    """A failing model call is a judge_error, not a campaign abort and not a retry."""
    from coding_agent.model.protocol import ModelTransportError

    gateway = FakeGateway(error=ModelTransportError(retryable=True, cause=RuntimeError("boom")))
    run = _run_document()

    judgement = await judge_run(
        run,
        build_transcript_excerpt(run),
        ModelSettings(model="claude-judge-2026"),
        gateway=gateway,
    )

    assert judgement.ok is False
    assert judgement.error == JUDGE_ERROR
    assert judgement.error_detail is not None and "judge request failed" in judgement.error_detail
    assert len(gateway.requests) == 1


@pytest.mark.asyncio
async def test_judge_run_rejects_an_excerpt_from_a_different_run() -> None:
    """Judging run A against run B's facts would publish a score for the wrong run."""
    run = _run_document(task_id="demo-task", repeat=1)
    other = build_transcript_excerpt(_run_document(task_id="other-task", repeat=1))

    judgement = await judge_run(
        run, other, ModelSettings(model="claude-judge-2026"), gateway=FakeGateway([VALID_RESPONSE])
    )

    assert judgement.ok is False
    assert judgement.error == JUDGE_ERROR


@pytest.mark.asyncio
async def test_judge_run_reuses_the_anthropic_messages_adapter_streaming_without_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The judge is the shipped adapter: one streaming request, no tools, no extras."""
    from coding_agent.model import anthropic_messages
    from tests.fixtures.anthropic_events import text_response_events

    messages: Any = _FakeMessages([text_response_events(VALID_RESPONSE)])
    constructor_calls: list[dict[str, object]] = []

    def constructor(**kwargs: object) -> Any:
        constructor_calls.append(kwargs)
        return SimpleNamespace(messages=messages)

    monkeypatch.setattr(anthropic_messages, "AsyncAnthropic", constructor)
    run = _run_document()
    settings = ModelSettings(model="claude-judge-2026", max_output_tokens=8192)

    judgement = await judge_run(run, build_transcript_excerpt(run), settings, api_key="secret-key")

    assert judgement.ok is True
    assert constructor_calls == [
        {
            "api_key": "secret-key",
            "base_url": settings.base_url,
            "max_retries": 0,
        }
    ]
    assert len(messages.calls) == 1
    payload = messages.calls[0]
    assert payload["model"] == "claude-judge-2026"
    assert payload["stream"] is True
    assert "tools" not in payload
    assert "tool_choice" not in payload
    assert payload["max_tokens"] <= 1024


# --- redaction and the excerpt ---------------------------------------------


def test_redact_text_removes_absolute_paths_and_credentials() -> None:
    """The judge prompt must never carry a local path or a credential."""
    hostile = (
        "I edited /tmp/pytest-123/campaign/runs/demo-task/repeat-1/workspace/src/mod.py "
        "and C:\\Users\\operator\\secrets.txt and ~/projects/private.key. "
        "The key sk-ant-api03-ABCDEF123456 failed, so I set api_key=topsecret and "
        "Authorization: Bearer abc123, then read https://api.anthropic.com docs "
        "and edited src/textkit/slugify.py."
    )

    redacted = redact_text(hostile)

    for leak in (
        "/tmp/pytest-123",
        "C:\\Users",
        "~/projects",
        "sk-ant-api03",
        "topsecret",
        "Bearer abc123",
    ):
        assert leak not in redacted
    assert "https://api.anthropic.com" in redacted
    assert "src/textkit/slugify.py" in redacted


def test_build_transcript_excerpt_uses_only_run_v1_facts_and_redacts_the_message() -> None:
    """Nothing outside the run-v1 projection, and no raw path or credential, reaches the judge."""
    run = _run_document(
        agent_report={
            "schema_version": "run-report-v1",
            "final_assistant_text": (
                "Done. I verified with the suite at /tmp/secret/runs/demo-task/repeat-1 "
                "using api_key=hunter2."
            ),
            "notes": "workspace was /Users/spy/elsewhere",
        }
    )

    excerpt = build_transcript_excerpt(run)

    serialized = json.dumps(excerpt)
    assert excerpt["task_id"] == "demo-task"
    assert excerpt["category"] == "local_edit"
    assert excerpt["outcome"] == "OK"
    assert excerpt["strict_success"] is True
    assert excerpt["oracle"] == {"target_passed": True, "regression_passed": True}
    assert excerpt["tools"]["executed"] == 5
    assert excerpt["final_assistant_text"] is not None
    for leak in ("/tmp/secret", "hunter2", "/Users/spy", "notes", "workspace was"):
        assert leak not in serialized


def test_build_transcript_excerpt_reports_a_missing_final_message() -> None:
    """A run without an exported final message still yields a complete excerpt."""
    excerpt = build_transcript_excerpt(_run_document())

    assert excerpt["final_assistant_text"] is None
    assert excerpt["task_id"] == "demo-task"


def test_judge_prompt_names_the_three_scores_and_the_fixed_json_contract() -> None:
    """The prompt fixes the vocabulary and the output shape the parser relies on."""
    run = _run_document()
    prompt = judge_prompt(build_transcript_excerpt(run, "Done and verified."))

    assert "task_completion" in prompt
    assert "process_quality" in prompt
    assert "communication" in prompt
    assert '"rationale"' in prompt
    assert "Done and verified." in prompt
    for leak in (str(Path(__file__).resolve().parent), "api_key", "ANTHROPIC"):
        assert leak not in prompt


# --- the judgement record ---------------------------------------------------


def test_judgement_document_matches_the_published_schema() -> None:
    """A published judgement must carry exactly the schema's fields."""
    judgement = parse_judgement(VALID_RESPONSE, judge_model="claude-judge-2026")
    schema = json.loads((SCHEMAS / "judgement-v1.schema.json").read_text(encoding="utf-8"))

    document = judgement.to_document(campaign_id="campaign-1", task_id="demo-task", repeat=1)

    assert document["schema_version"] == JUDGEMENT_SCHEMA_VERSION
    assert set(schema["required"]) <= set(document)
    assert set(document) == set(schema["properties"])
    assert document["judge_model"] == "claude-judge-2026"
    assert document["prompt_version"] == PROMPT_VERSION


def test_write_judgement_never_overwrites_an_existing_record(tmp_path: Path) -> None:
    """Judgement records are as immutable as run records."""
    from coding_agent.evaluation.judge import JudgeWriteError

    judgement = parse_judgement(VALID_RESPONSE, judge_model="claude-judge-2026")
    path = tmp_path / "judgement.json"
    write_judgement(path, judgement, campaign_id="c", task_id="demo-task", repeat=1)

    with pytest.raises(JudgeWriteError, match="already"):
        write_judgement(path, judgement, campaign_id="c", task_id="demo-task", repeat=1)


# --- summary aggregation ----------------------------------------------------


def test_summarize_aggregates_judge_means_coverage_and_errors() -> None:
    """Fuzzy scores are reported next to the deterministic ones, never inside them."""
    run = _make_strict_run()
    documents = [
        _judgement_document(
            "demo-task", 1, scores={"task_completion": 4, "process_quality": 4, "communication": 2}
        ),
        _judgement_document(
            "demo-task", 2, scores={"task_completion": 5, "process_quality": 3, "communication": 3}
        ),
        _judgement_document("demo-task", 3, error="the judge request failed: ModelTransportError"),
    ]
    second = _make_strict_run(task_id="demo-task", repeat=2)
    third = _make_strict_run(task_id="demo-task", repeat=3)

    summary = summarize([run, second, third], judgements=documents)

    assert summary.started_runs == 3
    assert summary.strict_success_runs == 3
    assert summary.judged_runs == 3
    assert summary.judge_error_runs == 1
    assert summary.judge_means == {
        "task_completion": 4.5,
        "process_quality": 3.5,
        "communication": 2.5,
    }
    assert summary.judge_coverage == 1.0


def test_summarize_without_judgements_reports_an_empty_judge_section() -> None:
    """The no-judge path must aggregate exactly as before, with empty judge fields."""
    summary = summarize([_make_strict_run()])

    assert summary.judged_runs == 0
    assert summary.judge_error_runs == 0
    assert summary.judge_means == {
        "task_completion": None,
        "process_quality": None,
        "communication": None,
    }
    assert summary.judge_coverage == 0.0


def test_summarize_campaign_reads_the_judgement_records(tmp_path: Path) -> None:
    """The documented aggregation command joins runs.jsonl with judgement.json files."""
    input_dir = tmp_path / "campaign"
    input_dir.mkdir()
    (input_dir / "runs.jsonl").write_text(
        json.dumps(run_document(_make_strict_run(), campaign_id="campaign-1")) + "\n",
        encoding="utf-8",
    )
    run_dir = input_dir / "runs" / "demo-task" / "repeat-1"
    run_dir.mkdir(parents=True)
    (run_dir / "judgement.json").write_text(
        json.dumps(
            _judgement_document(
                "demo-task",
                1,
                scores={"task_completion": 4, "process_quality": 5, "communication": 4},
            )
        ),
        encoding="utf-8",
    )

    summary = summarize_campaign(input_dir, tmp_path / "reports")

    assert summary.judged_runs == 1
    assert summary.judge_error_runs == 0
    assert summary.judge_means == {
        "task_completion": 4.0,
        "process_quality": 5.0,
        "communication": 4.0,
    }
    document = json.loads((tmp_path / "reports" / "summary.json").read_text(encoding="utf-8"))
    assert document["judged_runs"] == 1
    assert document["judge_means"]["task_completion"] == 4.0


# --- the runner hook ---------------------------------------------------------


class StubAgent:
    """Stand in for the agent process: apply the gold overlay, emit a fixed report."""

    def __init__(self, manifest: Any) -> None:
        self._manifest = manifest

    def __call__(self, invocation: Any) -> AgentProcessResult:
        task = next(item for item in self._manifest.tasks if item.task_id == invocation.task_id)
        shutil.copytree(task.gold_overlay, invocation.workspace, dirs_exist_ok=True)
        invocation.report_out.write_text(
            json.dumps(
                {
                    "schema_version": "run-report-v1",
                    "state": "COMPLETED",
                    "stop_reason": "COMPLETED",
                    "final_assistant_text": (
                        "Created the requested helper and verified it with the existing suite."
                    ),
                    "model_identity": {"name": "claude-stub-model-2026"},
                    "model": {
                        "main": {
                            "requests": 2,
                            "attempts": 2,
                            "network_retries": 0,
                            "usage_coverage": None,
                            "usage": {
                                "input_tokens": 30,
                                "output_tokens": 9,
                                "cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 0,
                            },
                        },
                        "compaction": {
                            "requests": 0,
                            "attempts": 0,
                            "network_retries": 0,
                            "usage_coverage": None,
                            "usage": {
                                "input_tokens": None,
                                "output_tokens": None,
                                "cache_creation_input_tokens": None,
                                "cache_read_input_tokens": None,
                            },
                        },
                    },
                    "tools": {
                        "proposed": 1,
                        "executed": 1,
                        "succeeded": 1,
                        "failed": 0,
                        "skipped": 0,
                        "duplicate_calls": 0,
                        "output_bytes": 4,
                        "truncated": 0,
                    },
                    "compaction": {"count": 0, "requests": 0, "above_target": False},
                    "durations": {
                        "agent_monotonic_ms": 11,
                        "retry_wait_monotonic_ms": 0,
                        "tool_execution_monotonic_ms": 4,
                    },
                }
            ),
            encoding="utf-8",
        )
        return AgentProcessResult(exit_code=0, timed_out=False)


def test_run_campaign_judge_hook_writes_judgement_records(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """`run --judge` scores every finished run and feeds the summary aggregation."""
    manifest = validate_manifest(write_manifest(manifest_root))
    gateway = FakeGateway(responses=[VALID_RESPONSE])
    hook = build_judge_hook(config_file, gateway=gateway, environ={"ANTHROPIC_API_KEY": "test-key"})
    output_dir = tmp_path / "campaign"

    result = run_campaign(
        manifest,
        config_file,
        1,
        output_dir,
        False,
        agent_launcher=StubAgent(manifest),
        agent_executable=("fake-agent",),
        judge=hook,
    )

    record = json.loads(
        (output_dir / "runs" / "demo-task" / "repeat-1" / "judgement.json").read_text(
            encoding="utf-8"
        )
    )
    summary = result.summary
    assert summary is not None
    assert record["schema_version"] == JUDGEMENT_SCHEMA_VERSION
    assert record["campaign_id"] == result.campaign_id
    assert record["task_id"] == "demo-task"
    assert record["scores"]["task_completion"] == 4
    assert record["error"] is None
    assert summary.judged_runs == 1
    assert summary.judge_error_runs == 0
    assert summary.judge_means == {
        "task_completion": 4.0,
        "process_quality": 5.0,
        "communication": 3.0,
    }
    assert len(gateway.requests) == 1
    prompt = gateway.requests[0].messages[0].parts[0].text
    assert "demo-task" in prompt
    assert str(tmp_path) not in prompt
    assert "Created the requested helper" in prompt


def test_run_campaign_judge_errors_never_abort_the_campaign(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """A judge that cannot answer leaves the deterministic campaign untouched."""
    manifest = validate_manifest(write_manifest(manifest_root))
    gateway = FakeGateway(responses=["garbage", "garbage"])
    hook = build_judge_hook(config_file, gateway=gateway, environ={"ANTHROPIC_API_KEY": "test-key"})
    output_dir = tmp_path / "campaign"

    result = run_campaign(
        manifest,
        config_file,
        1,
        output_dir,
        False,
        agent_launcher=StubAgent(manifest),
        agent_executable=("fake-agent",),
        judge=hook,
    )

    record = json.loads(
        (output_dir / "runs" / "demo-task" / "repeat-1" / "judgement.json").read_text(
            encoding="utf-8"
        )
    )
    summary = result.summary
    assert summary is not None
    assert record["error"] == JUDGE_ERROR
    assert record["scores"] == {}
    assert summary.started_runs == 1
    assert summary.strict_success_runs == 1
    assert summary.judged_runs == 1
    assert summary.judge_error_runs == 1
    assert summary.judge_means == {
        "task_completion": None,
        "process_quality": None,
        "communication": None,
    }


def test_run_campaign_without_judge_writes_no_judgement_records(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
) -> None:
    """The default path behaves exactly as before: no judgement.json anywhere."""
    manifest = validate_manifest(write_manifest(manifest_root))
    output_dir = tmp_path / "campaign"

    result = run_campaign(
        manifest,
        config_file,
        1,
        output_dir,
        False,
        agent_launcher=StubAgent(manifest),
        agent_executable=("fake-agent",),
    )

    summary = result.summary
    assert summary is not None
    assert summary.judged_runs == 0
    assert summary.judge_error_runs == 0
    assert list(output_dir.rglob("judgement.json")) == []
    assert summary.strict_success_runs == 1


def test_cli_run_judge_requires_the_model_credential(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A judged campaign without the API key fails fast instead of mid-campaign."""
    path = write_manifest(manifest_root, tasks=task_table("demo-task", manifest_root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    exit_code = evaluation_cli.main(
        [
            "run",
            "--manifest",
            str(path),
            "--config",
            str(config_file),
            "--out",
            str(tmp_path / "out"),
            "--judge",
        ]
    )

    assert exit_code == 2
    assert "EVALUATION_ERROR" in capsys.readouterr().err


def test_cli_run_judge_dry_run_makes_no_model_call(
    manifest_root: Path,
    config_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A dry run with --judge still plans without touching the judge."""
    manifest = validate_manifest(write_manifest(manifest_root))

    exit_code = evaluation_cli.main(
        [
            "run",
            "--manifest",
            str(manifest.path),
            "--config",
            str(config_file),
            "--out",
            str(tmp_path / "out"),
            "--dry-run",
            "--judge",
        ]
    )

    assert exit_code == 0
    assert "dry run" in capsys.readouterr().out


# --- helpers ----------------------------------------------------------------


class _FakeStream:
    def __init__(self, events: list[Any]) -> None:
        self._events = events
        self.closed = False

    def __aiter__(self) -> Any:
        return self._iterate()

    async def _iterate(self) -> Any:
        for event in self._events:
            yield event

    async def close(self) -> None:
        self.closed = True


class _FakeMessages:
    def __init__(self, streams: list[Any]) -> None:
        self._streams = list(streams)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> Any:
        self.calls.append(kwargs)
        outcome = self._streams.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeStream(outcome)


def _run_document(
    task_id: str = "demo-task",
    repeat: int = 1,
    agent_report: dict[str, object] | None = None,
) -> dict[str, object]:
    run = _make_strict_run(task_id=task_id, repeat=repeat)
    if agent_report is not None:
        run.agent_report = agent_report
    return run_document(run, campaign_id="campaign-1")


def _make_strict_run(task_id: str = "demo-task", repeat: int = 1) -> RunResult:
    run = RunResult(task_id=task_id, category="local_edit", repeat=repeat)
    run.oracle_passed = True
    run.regressions_passed = True
    run.target_oracle = OracleFacts(passed=True, exit_code=0, duration_ms=5, errored=False)
    run.regression_oracle = OracleFacts(passed=True, exit_code=0, duration_ms=5, errored=False)
    run.state = "COMPLETED"
    run.model.usage.input_tokens = 40
    run.model.usage.output_tokens = 12
    run.tools.executed = 5
    run.tools.succeeded = 5
    run.durations.agent_monotonic_ms = 100
    run.hashes = {
        "config": "c",
        "task": "t",
        "prompt": "p",
        "tool_schema": "s",
        "baseline_tree": "b",
        "workspace_tree": "w",
        "diff": "d",
    }
    score_result(run)
    return run


def _judgement_document(
    task_id: str,
    repeat: int,
    *,
    scores: dict[str, int] | None = None,
    error: str | None = None,
) -> dict[str, object]:
    if error is not None:
        judgement = Judgement(
            scores={},
            rationale="",
            judge_model="claude-judge-2026",
            prompt_version=PROMPT_VERSION,
            error=JUDGE_ERROR,
            error_detail=error,
        )
    else:
        judgement = Judgement(
            scores=scores or {},
            rationale="Scored from the run facts.",
            judge_model="claude-judge-2026",
            prompt_version=PROMPT_VERSION,
        )
    return judgement.to_document(campaign_id="campaign-1", task_id=task_id, repeat=repeat)
