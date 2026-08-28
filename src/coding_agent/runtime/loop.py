"""The single provider-neutral model/tool loop."""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from coding_agent.config import AppSettings, ConfigurationError
from coding_agent.context import (
    CompactionRequired,
    Compactor,
    ContextBuilder,
    ContextOverflow,
    ContextRequest,
    ReadyContext,
)
from coding_agent.core.cancellation import CancellationToken
from coding_agent.core.errors import CancellationRequested, StoreError
from coding_agent.core.events import RunOutcome
from coding_agent.core.models import (
    ApprovalDecision,
    AssistantTurn,
    EffectStartResult,
    ErrorKind,
    ModelStopReason,
    RunState,
    StopReason,
    TextPart,
    ToolError,
    ToolResult,
    Usage,
)
from coding_agent.model import (
    ModelAPIError,
    ModelGateway,
    ModelProtocolError,
    ModelRequest,
    ModelTransportError,
    TextDelta,
)
from coding_agent.model.retry import RetryingInvoker
from coding_agent.runtime.approval import ApprovalGate
from coding_agent.runtime.coordinator import RunMutationGate
from coding_agent.runtime.metrics import model_config_hash
from coding_agent.runtime.publisher import AssistantDelta, EventPublisher, ToolOutputDelta
from coding_agent.storage.sqlite import SQLiteStore
from coding_agent.tools import ToolContext, error_result
from coding_agent.tools.paths import WorkspaceBoundary
from coding_agent.tools.registry import ToolRegistry

_LOGGER = logging.getLogger(__name__)


class AgentLoop:
    """Drive complete model turns and serialized local tool calls until a typed outcome."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        context_builder: ContextBuilder,
        compactor: Compactor,
        model: ModelGateway,
        invoker: RetryingInvoker,
        tools: ToolRegistry,
        approval_gate: ApprovalGate,
        publisher: EventPublisher,
        mutation_gate: RunMutationGate,
        settings: AppSettings,
    ) -> None:
        self._store = store
        self._context_builder = context_builder
        self._compactor = compactor
        self._model = model
        self._invoker = invoker
        self._tools = tools
        self._approval_gate = approval_gate
        self._publisher = publisher
        self._mutation_gate = mutation_gate
        self._settings = settings
        self._system_prompt = (
            files("coding_agent.prompts").joinpath("system.md").read_text(encoding="utf-8")
        )

    async def run(
        self,
        run_id: str,
        session_id: str,
        cancellation: CancellationToken,
    ) -> RunOutcome:
        empty_responses = 0
        repeated_fingerprint: str | None = None
        repetition_count = 0
        argument_error_rounds = 0
        provider_overflow_rebuilt = False
        try:
            await self._mutation_gate.register_cancellation(run_id, cancellation)
            cancellation.raise_if_cancelled()
            await self._transition(
                run_id, session_id, {RunState.STARTING}, RunState.BUILDING_CONTEXT
            )
            for round_no in range(1, self._settings.agent.max_rounds + 1):
                cancellation.raise_if_cancelled()
                try:
                    context = await self._build_context(
                        run_id, session_id, cancellation, round_no=round_no
                    )
                except ConfigurationError:
                    return await self._finish(
                        run_id,
                        session_id,
                        RunOutcome.fail(StopReason.CONFIG_ERROR, ErrorKind.CONFIG_ERROR),
                    )
                if isinstance(context, RunOutcome):
                    return context
                await self._transition(
                    run_id,
                    session_id,
                    {RunState.BUILDING_CONTEXT},
                    RunState.MODEL_STREAMING,
                )
                final_round = round_no == self._settings.agent.max_rounds
                request = ModelRequest(
                    system=(
                        context.view.system
                        + (
                            "\n\nDirectly summarize the current result without tools."
                            if final_round
                            else ""
                        )
                    ),
                    messages=context.view.messages,
                    tools=() if final_round else tuple(context.view.tool_schemas),
                    max_tokens=self._settings.model.max_output_tokens,
                )
                try:
                    turn = await self._complete(round_no, run_id, session_id, request, cancellation)
                except CancellationRequested:
                    raise
                except ModelAPIError as error:
                    outcome = (
                        RunOutcome.fail(StopReason.AUTH_ERROR, ErrorKind.AUTH_ERROR)
                        if error.status_code in {401, 403}
                        else RunOutcome.fail(StopReason.RETRY_EXHAUSTED, ErrorKind.RETRY_EXHAUSTED)
                    )
                    return await self._finish(run_id, session_id, outcome)
                except ModelTransportError:
                    return await self._finish(
                        run_id,
                        session_id,
                        RunOutcome.fail(StopReason.RETRY_EXHAUSTED, ErrorKind.RETRY_EXHAUSTED),
                    )
                except ModelProtocolError as error:
                    if error.code == "INCOMPLETE_TOOL_CALL":
                        return await self._finish(
                            run_id,
                            session_id,
                            RunOutcome.stop(StopReason.INCOMPLETE_TOOL_CALL),
                        )
                    return await self._finish(
                        run_id,
                        session_id,
                        RunOutcome.fail(
                            StopReason.MODEL_PROTOCOL_ERROR,
                            ErrorKind.MODEL_PROTOCOL_ERROR,
                        ),
                    )
                except ConfigurationError:
                    return await self._finish(
                        run_id,
                        session_id,
                        RunOutcome.fail(StopReason.CONFIG_ERROR, ErrorKind.CONFIG_ERROR),
                    )

                cancellation.raise_if_cancelled()

                if turn.stop_reason is ModelStopReason.MODEL_CONTEXT_WINDOW_EXCEEDED:
                    if provider_overflow_rebuilt:
                        return await self._finish(
                            run_id,
                            session_id,
                            RunOutcome.fail(
                                StopReason.CONTEXT_OVERFLOW, ErrorKind.CONTEXT_OVERFLOW
                            ),
                        )
                    provider_overflow_rebuilt = True
                    await self._transition(
                        run_id,
                        session_id,
                        {RunState.MODEL_STREAMING},
                        RunState.BUILDING_CONTEXT,
                    )
                    rebuilt = await self._build_context(
                        run_id,
                        session_id,
                        cancellation,
                        round_no=round_no,
                        force_compaction=True,
                    )
                    if isinstance(rebuilt, RunOutcome):
                        return rebuilt
                    continue

                special = await self._special_outcome(
                    run_id, session_id, turn, final_round=final_round
                )
                if special is not None:
                    return special

                if not turn.tool_calls and not _has_nonempty_text(turn):
                    empty_responses += 1
                    if empty_responses > 1:
                        return await self._finish(
                            run_id,
                            session_id,
                            RunOutcome.stop(StopReason.EMPTY_RESPONSE),
                        )
                    await self._transition(
                        run_id,
                        session_id,
                        {RunState.MODEL_STREAMING},
                        RunState.BUILDING_CONTEXT,
                    )
                    continue
                empty_responses = 0
                if not turn.tool_calls:
                    outcome = (
                        RunOutcome.stop(StopReason.MAX_ROUNDS)
                        if final_round
                        else RunOutcome.complete()
                    )
                    return await self._mutation_gate.commit_final_turn(run_id, turn, outcome)

                fingerprint = _tool_fingerprint(turn)
                if fingerprint == repeated_fingerprint:
                    repetition_count += 1
                else:
                    repeated_fingerprint = fingerprint
                    repetition_count = 1
                if repetition_count > self._settings.agent.doom_loop_threshold:
                    return await self._finish(
                        run_id, session_id, RunOutcome.stop(StopReason.DOOM_LOOP)
                    )

                workspace = self._workspace_boundary(session_id)
                group = await self._mutate(
                    session_id, lambda: self._store.stage_tool_group(run_id, turn)
                )
                if repetition_count == self._settings.agent.doom_loop_threshold:
                    repeated_results = tuple(
                        ToolResult(
                            call.call.id,
                            "repeated identical tool call was not executed",
                            False,
                            ToolError(
                                "REPETITION_DETECTED",
                                "repeated identical tool call was not executed",
                            ),
                        )
                        for call in group.calls
                    )
                    await self._mutate(
                        session_id,
                        lambda: self._store.settle_tool_group(group.id, repeated_results),
                    )
                    await self._transition(
                        run_id,
                        session_id,
                        {RunState.MODEL_STREAMING},
                        RunState.BUILDING_CONTEXT,
                    )
                    continue
                argument_error = False
                for call in group.calls:
                    cancellation.raise_if_cancelled()
                    unusable = turn.invalid_tool_arguments.get(call.call.id)
                    if unusable is not None:
                        # Spec 8.3: the identity is valid, so the model gets a tool error to
                        # correct rather than a dead run, and the call is never prepared.
                        argument_error = True
                        rejected = error_result(
                            call.call.id, call.call.name, unusable.code, unusable.message
                        )
                        await self._mutate(
                            session_id,
                            lambda result=rejected: self._store.settle_tool_group(
                                group.id, (result,)
                            ),
                        )
                        continue
                    prepared = self._tools.prepare(call.call, workspace)
                    if isinstance(prepared, ToolResult):
                        if prepared.error is not None and prepared.error.code.startswith("INVALID"):
                            argument_error = True
                        await self._mutate(
                            session_id,
                            lambda result=prepared: self._store.settle_tool_group(
                                group.id, (result,)
                            ),
                        )
                        continue
                    if prepared.requires_approval:
                        await self._mutate(
                            session_id,
                            lambda: self._store.request_approval(run_id, prepared),
                        )
                        decision = await self._approval_gate.request(prepared, cancellation)
                        if not self._approval_gate.is_persisted(prepared.call.id):
                            await self._mutation_gate.resolve_approval(
                                run_id,
                                prepared.call.id,
                                decision,
                                f"loop-approval-{prepared.call.id}",
                            )
                        if decision is ApprovalDecision.REJECT:
                            break
                    effect = await self._mutation_gate.begin_effect(run_id, prepared.call.id)
                    if effect is not EffectStartResult.STARTED:
                        cancellation.raise_if_cancelled()
                        raise CancellationRequested()
                    try:

                        async def emit_tool_output(
                            text: str,
                            *,
                            tool_call_id: str = prepared.call.id,
                        ) -> None:
                            await self._publisher.publish_transient(
                                ToolOutputDelta(
                                    session_id=session_id,
                                    run_id=run_id,
                                    draft_epoch=f"tool:{tool_call_id}",
                                    tool_call_id=tool_call_id,
                                    text=text,
                                )
                            )

                        result = await self._tools.execute(
                            prepared,
                            ToolContext(
                                workspace=workspace,
                                cancellation=cancellation,
                                emit_output=emit_tool_output,
                            ),
                        )
                    except CancellationRequested:
                        message = "tool execution was cancelled"
                        cancelled_result = ToolResult(
                            prepared.call.id,
                            message,
                            False,
                            ToolError("TOOL_CANCELLED", message),
                        )
                        await self._mutate(
                            session_id,
                            lambda: self._store.settle_tool_group(group.id, (cancelled_result,)),
                        )
                        raise
                    await self._mutate(
                        session_id,
                        lambda result=result: self._store.settle_tool_group(group.id, (result,)),
                    )
                cancellation.raise_if_cancelled()
                if argument_error:
                    argument_error_rounds += 1
                else:
                    argument_error_rounds = 0
                current_state = self._store.get_run(run_id).state
                await self._transition(
                    run_id, session_id, {current_state}, RunState.BUILDING_CONTEXT
                )
                if argument_error_rounds > self._settings.agent.tool_argument_retries:
                    return await self._finish(
                        run_id, session_id, RunOutcome.stop(StopReason.DOOM_LOOP)
                    )

            return await self._finish(run_id, session_id, RunOutcome.stop(StopReason.MAX_ROUNDS))
        except CancellationRequested:
            current = self._store.get_run(run_id)
            if current.state is not RunState.CANCELLING:
                await self._mutate(
                    session_id,
                    lambda: self._store.request_cancellation(
                        run_id, f"loop-cancel-{run_id}", f"loop-cancel-{run_id}"
                    ),
                )
            return await self._finish(run_id, session_id, RunOutcome.cancel())
        except StoreError as error:
            if cancellation.cancelled:
                return await self._finish(run_id, session_id, RunOutcome.cancel())
            return await self._fail_unexpected(run_id, session_id, error)
        except Exception as error:
            return await self._fail_unexpected(run_id, session_id, error)
        finally:
            await self._mutation_gate.unregister_cancellation(run_id)

    async def _fail_unexpected(
        self,
        run_id: str,
        session_id: str,
        error: Exception,
    ) -> RunOutcome:
        """Give an unexpected failure a terminal record and release the active-run claim.

        A local environment or persistence failure is this process's own fault, so it ends
        as ``INTERNAL_ERROR`` rather than blaming the operator's configuration; the
        original exception stays visible with its traceback in the process log.
        """
        _LOGGER.error(
            "run failed with an unexpected error",
            exc_info=error,
            extra={"run_id": run_id, "session_id": session_id},
        )
        return await self._finish(
            run_id,
            session_id,
            RunOutcome.fail(StopReason.INTERNAL_ERROR, ErrorKind.INTERNAL_ERROR),
        )

    def _workspace_boundary(self, session_id: str) -> WorkspaceBoundary:
        return WorkspaceBoundary(
            Path(self._store.load_snapshot(session_id).session.workspace_realpath)
        )

    def _compiled_system(self, session_id: str) -> str:
        """Compile the developer instructions with the current environment.

        Spec 7.2 and 7.3 item 1 make environment information mandatory in every model
        view, so it belongs in the system string the context estimator measures. Only
        facts this process already owns appear here: the session workspace root, the
        platform and the configured tool limits, never a credential or its value.
        """
        tools = self._settings.tools
        workspace_root = self._store.load_snapshot(session_id).session.workspace_realpath
        return "\n".join(
            (
                self._system_prompt.rstrip(),
                "",
                "Current environment:",
                f"- workspace root: {workspace_root}",
                f"- platform: {sys.platform}",
                f"- read_file returns at most {tools.read_max_lines} lines"
                f" or {tools.read_max_bytes} bytes per call",
                f"- run_command times out after {tools.command_timeout_seconds}s"
                f" and truncates output at {tools.command_output_bytes} bytes",
            )
        )

    async def _build_context(
        self,
        run_id: str,
        session_id: str,
        cancellation: CancellationToken,
        *,
        round_no: int,
        force_compaction: bool = False,
    ) -> ReadyContext | RunOutcome:
        base_request = ContextRequest(
            system=self._compiled_system(session_id),
            context_window=self._settings.model.context_window,
            max_output_tokens=self._settings.model.max_output_tokens,
            safety_margin_tokens=self._settings.context.safety_margin_tokens,
            compact_trigger_ratio=self._settings.context.compact_trigger_ratio,
            compact_target_ratio=self._settings.context.compact_target_ratio,
            summary_max_tokens=self._settings.context.summary_max_tokens,
            recent_user_turns=self._settings.context.recent_turns_min,
            current_run_id=run_id,
            tool_schemas=tuple(self._tools.schemas()),
        )
        request = (
            replace(
                base_request,
                compact_trigger_ratio=0.000_001,
                compact_target_ratio=0.000_001,
            )
            if force_compaction
            else base_request
        )
        result = self._context_builder.build(
            self._store.load_committed_transcript(session_id),
            self._store.load_context_snapshot(session_id),
            request,
        )
        if isinstance(result, ContextOverflow):
            return await self._finish(
                run_id,
                session_id,
                RunOutcome.fail(StopReason.CONTEXT_OVERFLOW, ErrorKind.CONTEXT_OVERFLOW),
            )
        if isinstance(result, CompactionRequired):
            await self._transition(
                run_id, session_id, {RunState.BUILDING_CONTEXT}, RunState.COMPACTING
            )
            compacted = await self._compactor.compact(
                result.plan,
                cancellation,
                invoke=lambda model_request, token: self._complete(
                    round_no,
                    run_id,
                    session_id,
                    model_request,
                    token,
                    kind="compaction",
                ),
            )
            cancellation.raise_if_cancelled()
            await self._transition(
                run_id, session_id, {RunState.COMPACTING}, RunState.BUILDING_CONTEXT
            )
            if compacted.error is not None:
                return await self._finish(
                    run_id,
                    session_id,
                    RunOutcome.fail(StopReason.CONTEXT_OVERFLOW, ErrorKind.CONTEXT_OVERFLOW),
                )
            rebuilt = self._context_builder.build(
                self._store.load_committed_transcript(session_id),
                compacted.snapshot,
                base_request,
            )
            if not isinstance(rebuilt, ReadyContext):
                return await self._finish(
                    run_id,
                    session_id,
                    RunOutcome.fail(StopReason.CONTEXT_OVERFLOW, ErrorKind.CONTEXT_OVERFLOW),
                )
            return rebuilt
        return result

    async def _complete(
        self,
        round_no: int,
        run_id: str,
        session_id: str,
        request: ModelRequest,
        cancellation: CancellationToken,
        *,
        kind: str = "main",
    ) -> AssistantTurn:
        request_id = self._store.start_model_request(
            run_id,
            round_no,
            kind,
            self._settings.model.model,
            model_config_hash(self._settings.model, request),
        )
        attempts = 0
        retries = 0
        total_wait_ms = 0

        async def operation() -> AssistantTurn:
            nonlocal attempts
            if (
                kind == "main"
                and attempts
                and self._store.get_run(run_id).state is RunState.RETRY_WAIT
            ):
                await self._transition(
                    run_id,
                    session_id,
                    {RunState.RETRY_WAIT},
                    RunState.MODEL_STREAMING,
                )
            attempts += 1
            draft_epoch = f"{request_id}:{attempts}"
            draft: dict[int, list[str]] = {}

            async def collect_delta(delta: TextDelta) -> None:
                draft.setdefault(delta.index, []).append(delta.text)
                await self._publisher.publish_transient(
                    AssistantDelta(
                        session_id=session_id,
                        run_id=run_id,
                        draft_epoch=draft_epoch,
                        index=delta.index,
                        text=delta.text,
                    )
                )

            try:
                return await self._model.complete(request, collect_delta, cancellation)
            except BaseException:
                parts = tuple(
                    TextPart("".join(draft[index])) for index in sorted(draft) if draft[index]
                )
                if parts:
                    interrupted = AssistantTurn(
                        id=f"interrupted-{uuid4()}",
                        parts=parts,
                        stop_reason=ModelStopReason.END_TURN,
                        usage=Usage(),
                    )
                    await self._mutate(
                        session_id,
                        lambda: self._store.record_interrupted_turn(run_id, interrupted),
                    )
                raise

        async def on_retry(notice) -> None:
            nonlocal retries, total_wait_ms
            retries += 1
            total_wait_ms += round(notice.delay_seconds * 1000)
            if kind == "main":
                await self._mutate(
                    session_id,
                    lambda: self._store.schedule_model_retry(run_id, notice.event_payload),
                )

        try:
            turn = await self._invoker.invoke(operation, cancellation, on_retry)
        except BaseException as error:
            usage = error.usage if isinstance(error, ModelProtocolError) else None
            self._store.finish_model_request(
                request_id,
                result=(
                    "incomplete_tool_call"
                    if isinstance(error, ModelProtocolError)
                    and error.code == "INCOMPLETE_TOOL_CALL"
                    else "failed"
                ),
                usage=usage,
                attempt_count=max(1, attempts),
                network_retry_count=retries,
                total_wait_ms=total_wait_ms,
            )
            raise
        self._store.finish_model_request(
            request_id,
            result="succeeded",
            usage=turn.usage,
            attempt_count=attempts,
            network_retry_count=retries,
            total_wait_ms=total_wait_ms,
        )
        return turn

    async def _special_outcome(
        self,
        run_id: str,
        session_id: str,
        turn: AssistantTurn,
        *,
        final_round: bool,
    ) -> RunOutcome | None:
        if turn.stop_reason is ModelStopReason.TOOL_USE and not turn.tool_calls:
            return await self._finish(
                run_id,
                session_id,
                RunOutcome.fail(StopReason.MODEL_PROTOCOL_ERROR, ErrorKind.MODEL_PROTOCOL_ERROR),
            )
        if turn.stop_reason is ModelStopReason.MAX_TOKENS:
            reason = (
                StopReason.INCOMPLETE_TOOL_CALL if turn.tool_calls else StopReason.OUTPUT_TRUNCATED
            )
            await self._mutate(
                session_id, lambda: self._store.record_interrupted_turn(run_id, turn)
            )
            return await self._finish(run_id, session_id, RunOutcome.stop(reason))
        if turn.stop_reason is ModelStopReason.REFUSAL:
            await self._mutate(
                session_id, lambda: self._store.record_interrupted_turn(run_id, turn)
            )
            return await self._finish(run_id, session_id, RunOutcome.stop(StopReason.MODEL_REFUSAL))
        if turn.stop_reason is ModelStopReason.PAUSE_TURN:
            await self._mutate(
                session_id, lambda: self._store.record_interrupted_turn(run_id, turn)
            )
            return await self._finish(run_id, session_id, RunOutcome.stop(StopReason.PAUSE_TURN))
        if final_round and turn.tool_calls:
            return await self._finish(
                run_id,
                session_id,
                RunOutcome(
                    RunState.STOPPED,
                    StopReason.MAX_ROUNDS,
                    ErrorKind.MODEL_PROTOCOL_ERROR,
                ),
            )
        return None

    async def _finish(self, run_id: str, session_id: str, outcome: RunOutcome) -> RunOutcome:
        _ = session_id
        return await self._mutation_gate.finish_run(run_id, outcome)

    async def _transition(
        self,
        run_id: str,
        session_id: str,
        expected: set[RunState],
        target: RunState,
        stop_reason: StopReason | None = None,
        error_kind: ErrorKind | None = None,
    ) -> None:
        await self._mutate(
            session_id,
            lambda: self._store.transition_run(run_id, expected, target, stop_reason, error_kind),
        )

    async def _mutate(self, session_id: str, action):
        previous = self._store.events_after(session_id, 0)
        previous_seq = previous[-1].seq if previous else 0
        result = action()
        for event in self._store.events_after(session_id, previous_seq):
            await self._publisher.publish_committed(event)
        return result


def _has_nonempty_text(turn: AssistantTurn) -> bool:
    return any(isinstance(part, TextPart) and part.text.strip() for part in turn.parts)


def _tool_fingerprint(turn: AssistantTurn) -> str:
    value = [{"name": call.name, "input": _jsonable(call.input)} for call in turn.tool_calls]
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if hasattr(value, "items"):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


__all__ = ["AgentLoop"]
