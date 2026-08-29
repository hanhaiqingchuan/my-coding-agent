"""The LLM judge for fuzzy evaluation metrics.

The judge scores one finished run on three 1-5 axes (task completion, process quality,
communication) with a rationale. It reuses the shipped ``AnthropicMessagesModel``
adapter — the same ``ModelSettings`` family, one streaming request, no tools — so the
evaluation adds no second model client and no third-party evaluation framework.

The judge never sees anything outside the run's own ``run-v1`` projection plus the
run's final assistant message. Both are reduced to a fixed excerpt first, and the one
free-form input (the final message) is redacted of absolute paths and credentials
before it enters the prompt. Fuzzy scores are reported next to the deterministic
metrics; they never enter ``strict_success`` (spec section 18.5).

A judge that answers outside the fixed JSON contract gets exactly one retry; a second
malformed answer — or a failing model call — becomes a recorded ``judge_error``
judgement that never aborts the campaign.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from coding_agent.config import ModelSettings
from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.models import AssistantTurn, TextPart
from coding_agent.model.anthropic_messages import AnthropicMessagesModel
from coding_agent.model.protocol import ModelGateway, ModelMessage, ModelRequest

JUDGEMENT_SCHEMA_VERSION = "judgement-v1"
PROMPT_VERSION = "judge-v1"
SCORE_NAMES = ("task_completion", "process_quality", "communication")
JUDGE_ERROR = "judge_error"
SCORE_MINIMUM = 1
SCORE_MAXIMUM = 5
JUDGE_MAX_OUTPUT_TOKENS = 1024

_SYSTEM_PROMPT = (
    "You are the judge of one finished run of a headless coding agent. You read a fixed "
    "factual excerpt of that run and score it. You never see the task prompt, the tool "
    "arguments, the command output or the workspace; score only what the excerpt states."
)

_REDACTION_PATTERN = re.compile(
    r"(?P<url>https?://\S+)"
    r"|(?P<credential>sk-ant-\S+"
    r"|\S*(?:api[_-]?key|token|secret|password)\s*[=:]\s*\S+"
    r"|authorization\s*:\s*(?:bearer\s+)?\S+"
    r"|bearer\s+\S+"
    r")"
    r"|(?P<windows_path>[A-Za-z]:\\\S+)"
    r"|(?P<home_path>~\S+)"
    r"|(?P<posix_path>(?<![\w.])/(?!/)\S+)",
    re.IGNORECASE,
)

_TOOL_EXCERPT_FIELDS = (
    "proposed",
    "executed",
    "succeeded",
    "failed",
    "skipped",
    "duplicate_calls",
    "truncated",
)


class JudgeParseError(ValueError):
    """Raised when a judge response is not the fixed JSON contract."""


class JudgeWriteError(RuntimeError):
    """Raised when a judgement record would overwrite an existing file."""


@dataclass(frozen=True, slots=True)
class Judgement:
    """One judge outcome: three scores with a rationale, or a recorded judge error."""

    scores: Mapping[str, int]
    rationale: str
    judge_model: str
    prompt_version: str
    schema_version: str = JUDGEMENT_SCHEMA_VERSION
    error: str | None = None
    error_detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_document(self, *, campaign_id: str, task_id: str, repeat: int) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "campaign_id": campaign_id,
            "task_id": task_id,
            "repeat": repeat,
            "judge_model": self.judge_model,
            "prompt_version": self.prompt_version,
            "scores": dict(self.scores),
            "rationale": self.rationale,
            "error": self.error,
            "error_detail": self.error_detail,
        }


def redact_text(text: str) -> str:
    """Replace absolute paths and credential-shaped values, keeping URLs and relative paths.

    This is the same discipline the run documents follow: no local absolute path and no
    credential may leave the machine that produced them. Relative workspace paths and
    provider URLs carry no local information and survive.
    """

    def replace(match: re.Match[str]) -> str:
        if match.group("url") is not None:
            return match.group("url")
        return "[redacted]"

    return _REDACTION_PATTERN.sub(replace, text)


def build_transcript_excerpt(
    run_document: Mapping[str, object],
    final_assistant_text: str | None = None,
) -> dict[str, object]:
    """Project one ``run-v1`` document into the judge's excerpt.

    Every field comes from the run document itself; nothing is re-derived and no new
    fact is collected. The final assistant message is taken from the explicit argument
    when given, otherwise from the agent report's optional ``final_assistant_text``
    field, and is redacted before it enters the excerpt.
    """
    document = _mapping(run_document)
    if final_assistant_text is None:
        candidate = _mapping(document.get("agent_report")).get("final_assistant_text")
        final_assistant_text = candidate if isinstance(candidate, str) else None
    oracle = _mapping(document.get("oracle"))
    tools = _mapping(document.get("tools"))
    model = _mapping(document.get("model"))
    usage = _mapping(model.get("usage"))
    durations = _mapping(document.get("durations"))
    modifications = _mapping(document.get("modifications"))
    return {
        "task_id": document.get("task_id"),
        "category": document.get("category"),
        "repeat": document.get("repeat"),
        "outcome": document.get("outcome"),
        "state": document.get("state"),
        "stop_reason": document.get("stop_reason"),
        "failure_stage": document.get("failure_stage"),
        "failure_kind": document.get("failure_kind"),
        "strict_success": document.get("strict_success"),
        "artifact_correct": document.get("artifact_correct"),
        "oracle": {
            "target_passed": _mapping(oracle.get("target")).get("passed"),
            "regression_passed": _mapping(oracle.get("regression")).get("passed"),
        },
        "tools": {name: tools.get(name) for name in _TOOL_EXCERPT_FIELDS},
        "model": {
            "main_requests": model.get("main_requests"),
            "attempts": model.get("attempts"),
            "network_retries": model.get("network_retries"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        },
        "durations_ms": {
            "agent": durations.get("agent_monotonic_ms"),
            "tool_execution": durations.get("tool_execution_monotonic_ms"),
            "retry_wait": durations.get("retry_wait_monotonic_ms"),
            "total": durations.get("total_ms"),
        },
        "modifications": {
            "files_added": modifications.get("files_added"),
            "files_modified": modifications.get("files_modified"),
            "files_deleted": modifications.get("files_deleted"),
            "lines_added": modifications.get("lines_added"),
            "lines_removed": modifications.get("lines_removed"),
            "forbidden_paths_modified": list(modifications.get("forbidden_paths_modified") or ()),
            "detected_workspace_escape": modifications.get("detected_workspace_escape"),
        },
        "final_assistant_text": (
            redact_text(final_assistant_text) if final_assistant_text is not None else None
        ),
    }


def judge_prompt(excerpt: Mapping[str, object]) -> str:
    """Render the one judge prompt from a fixed excerpt."""
    facts = json.dumps(dict(excerpt), ensure_ascii=False, sort_keys=True, indent=2)
    final_text = excerpt.get("final_assistant_text")
    message = (
        final_text
        if isinstance(final_text, str) and final_text
        else ("(the run exported no final assistant message)")
    )
    return "\n".join(
        (
            "Score one finished coding-agent run on three axes, each an integer from 1 to 5:",
            "",
            "- task_completion: did the run achieve the task's goal? The oracle facts in the",
            "  excerpt below are the deterministic ground truth.",
            "- process_quality: were the tool choices and their order sensible — reading",
            "  before writing, recovering after failures, no redundant calls?",
            "- communication: does the final assistant message report the work honestly and",
            "  briefly, and state how the change was verified?",
            "",
            "Run facts (deterministic, recorded by the evaluation harness):",
            facts,
            "",
            "Final assistant message:",
            message,
            "",
            "Respond with exactly one JSON object and nothing else:",
            '{"task_completion": <1-5>, "process_quality": <1-5>, "communication": <1-5>,'
            ' "rationale": "<one short paragraph>"}',
        )
    )


def parse_judgement(
    text: str,
    *,
    judge_model: str,
    prompt_version: str = PROMPT_VERSION,
) -> Judgement:
    """Parse one judge answer into a Judgement, rejecting anything off-contract."""
    payload = _extract_json(text)
    if not isinstance(payload, dict):
        raise JudgeParseError("the response is not a JSON object")
    expected = {*SCORE_NAMES, "rationale"}
    present = set(payload)
    missing = sorted(expected - present)
    if missing:
        raise JudgeParseError(f"missing field: {missing[0]}")
    unknown = sorted(present - expected)
    if unknown:
        raise JudgeParseError(f"unknown field: {unknown[0]}")
    scores: dict[str, int] = {}
    for name in SCORE_NAMES:
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise JudgeParseError(f"score {name} must be an integer")
        if not SCORE_MINIMUM <= value <= SCORE_MAXIMUM:
            raise JudgeParseError(
                f"score {name} must be between {SCORE_MINIMUM} and {SCORE_MAXIMUM}"
            )
        scores[name] = value
    rationale = payload["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        raise JudgeParseError("rationale must be a non-empty string")
    return Judgement(
        scores=scores,
        rationale=rationale,
        judge_model=judge_model,
        prompt_version=prompt_version,
    )


async def judge_run(
    run_document: Mapping[str, object],
    transcript_excerpt: Mapping[str, object],
    settings: ModelSettings,
    *,
    gateway: ModelGateway | None = None,
    api_key: str = "",
) -> Judgement:
    """Judge one run with a single-model conversation, never raising.

    The gateway defaults to the shipped Anthropic Messages adapter built from
    ``settings``. One malformed answer is retried once; a second malformed answer or a
    failing model call becomes a ``judge_error`` Judgement, because a fuzzy score must
    never abort a deterministic campaign.
    """
    mismatch = _identity_mismatch(run_document, transcript_excerpt)
    if mismatch is not None:
        return _error_judgement(settings.model, mismatch)
    model = gateway if gateway is not None else AnthropicMessagesModel(settings, api_key)
    prompt = judge_prompt(transcript_excerpt)
    detail = "the judge produced no answer"
    for _attempt in (1, 2):
        try:
            text = await _request_judgement(model, prompt, settings)
        except Exception as error:  # noqa: BLE001 - the judge never aborts the campaign
            return _error_judgement(
                settings.model, f"the judge request failed: {type(error).__name__}"
            )
        try:
            return parse_judgement(text, judge_model=settings.model)
        except JudgeParseError as error:
            detail = str(error)
    return _error_judgement(settings.model, f"malformed judge response after one retry: {detail}")


def write_judgement(
    path: Path,
    judgement: Judgement,
    *,
    campaign_id: str,
    task_id: str,
    repeat: int,
) -> None:
    """Persist one immutable judgement record, never overwriting an existing one."""
    if path.exists():
        raise JudgeWriteError(f"judgement: {path.name} already exists and must not be overwritten")
    document = judgement.to_document(campaign_id=campaign_id, task_id=task_id, repeat=repeat)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def _request_judgement(model: ModelGateway, prompt: str, settings: ModelSettings) -> str:
    """Send the one judge request: streaming, no tools, a small output budget."""
    chunks: list[str] = []
    request = ModelRequest(
        system=_SYSTEM_PROMPT,
        messages=(ModelMessage(role="user", parts=(TextPart(prompt),)),),
        tools=(),
        max_tokens=max(1, min(settings.max_output_tokens, JUDGE_MAX_OUTPUT_TOKENS)),
    )
    turn = await model.complete(
        request, lambda delta: chunks.append(delta.text), CancellationToken()
    )
    if chunks:
        return "".join(chunks)
    return _turn_text(turn)


def _turn_text(turn: AssistantTurn) -> str:
    return "".join(part.text for part in turn.parts if isinstance(part, TextPart))


def _identity_mismatch(
    run_document: Mapping[str, object],
    transcript_excerpt: Mapping[str, object],
) -> str | None:
    task_id = run_document.get("task_id")
    excerpt_task = transcript_excerpt.get("task_id")
    if isinstance(task_id, str) and excerpt_task != task_id:
        return "the transcript excerpt belongs to a different run"
    return None


def _error_judgement(judge_model: str, detail: str) -> Judgement:
    return Judgement(
        scores={},
        rationale="",
        judge_model=judge_model,
        prompt_version=PROMPT_VERSION,
        error=JUDGE_ERROR,
        error_detail=detail,
    )


def _extract_json(text: str) -> object:
    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced is not None:
        candidates.append(fenced.group(1))
    balanced = _first_balanced_object(text)
    if balanced is not None:
        candidates.append(balanced)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise JudgeParseError("the response is not JSON")


def _first_balanced_object(text: str) -> str | None:
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    return text[start : index + 1]
    return None


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


__all__ = [
    "JUDGE_ERROR",
    "JUDGEMENT_SCHEMA_VERSION",
    "JUDGE_MAX_OUTPUT_TOKENS",
    "PROMPT_VERSION",
    "SCORE_NAMES",
    "JudgeParseError",
    "JudgeWriteError",
    "Judgement",
    "build_transcript_excerpt",
    "judge_prompt",
    "judge_run",
    "parse_judgement",
    "redact_text",
    "write_judgement",
]
