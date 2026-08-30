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
    PreparedToolCall,
    RunContextEstimate,
    RunState,
    Session,
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
    ThinkingBlockClosed,
    ThinkingDelta,
)
from coding_agent.model.retry import RetryingInvoker, RetryNotice
from coding_agent.runtime.approval import ApprovalGate
from coding_agent.runtime.coordinator import RunMutationGate
from coding_agent.runtime.metrics import model_config_hash
from coding_agent.runtime.publisher import (
    AssistantDelta,
    AssistantThinkingClosed,
    AssistantThinkingDelta,
    EventPublisher,
    ToolOutputDelta,
)
from coding_agent.storage.sqlite import SQLiteStore
from coding_agent.tools import ToolContext, error_result
from coding_agent.tools.paths import WorkspaceBoundary
from coding_agent.tools.registry import ToolRegistry
from coding_agent.workspace_context import (
    SKILL_DIAGNOSTIC_EVENT_TYPE,
    WorkspaceScan,
    render_workspace_sections,
    scan_workspace,
)

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
        # Session-id -> {absolute target path -> sha256 of the bytes this session last
        # read or wrote}. Spec 10.3's freshness gate consults this view so write_file
        # never executes against content the model has not seen in its current form.
        self._content_fingerprints: dict[str, dict[str, str]] = {}
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
            scan = self._scan_workspace(session_id)
            self._tools.configure_skills(scan.skills)
            await self._mutate(
                session_id,
                lambda: self._store.record_diagnostic(
                    run_id,
                    "run.context_loaded",
                    {
                        "agents_md_path": scan.instructions_path,
                        "skills_discovered": [skill.name for skill in scan.skills],
                    },
                ),
            )
            for diagnostic in scan.diagnostics:
                await self._mutate(
                    session_id,
                    lambda item=diagnostic: self._store.record_diagnostic(
                        run_id, SKILL_DIAGNOSTIC_EVENT_TYPE, item.payload()
                    ),
                )
            for round_no in range(1, self._settings.agent.max_rounds + 1):
                cancellation.raise_if_cancelled()
                try:
                    context = await self._build_context(
                        run_id, session_id, cancellation, round_no=round_no, scan=scan
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
                        scan=scan,
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
                        # The mode is read per request, so a mid-run session toggle
                        # applies to every approval the loop asks for afterwards.
                        decision = await self._approval_gate.request(
                            prepared,
                            cancellation,
                            session_auto_approve=self._store.get_session(session_id).auto_approve,
                        )
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
                                content_fingerprints=self._session_fingerprints(session_id),
                            ),
                        )
                        if result.ok:
                            self._record_content_fingerprint(session_id, prepared, result)
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

    async def compact_session(
        self,
        session_id: str,
        client_command_id: str,
        payload_hash: str,
        cancellation: CancellationToken,
    ) -> Session:
        """Run one forced maintenance compaction with no run active (spec 7.4, 14).

        There is no run, so no run record or run state is touched: the command receipt,
        the active-run rejection and the session-level ``compaction.started`` event
        commit in one transaction, the compactor then runs against the committed
        transcript regardless of the 80% threshold, and ``compaction.finished`` closes
        the lifecycle with the before/after estimates (plus an error code when the
        compactor kept the previous snapshot).
        """
        duplicate = self._store.completed_command_resource(
            session_id, client_command_id, payload_hash, "session.compact"
        )
        if duplicate is not None:
            # An idempotent replay answers with the first command's outcome; a fresh
            # plan build could now find nothing left to compact and misreport it.
            return self._store.load_snapshot(session_id).session
        active_run = self._store.load_snapshot(session_id).active_run
        if active_run is not None:
            raise StoreError(
                "RUN_ALREADY_ACTIVE",
                "cannot compact while a run is active; the running loop owns compaction",
            )
        scan = self._scan_workspace(session_id)
        self._tools.configure_skills(scan.skills)
        base_request = self._context_request(session_id, scan, current_run_id=None)
        forced = replace(
            base_request,
            compact_trigger_ratio=0.000_001,
            compact_target_ratio=0.000_001,
        )
        result = self._context_builder.build(
            self._store.load_committed_transcript(session_id),
            self._store.load_context_snapshot(session_id),
            forced,
        )
        if isinstance(result, ContextOverflow):
            raise StoreError(
                "CONTEXT_OVERFLOW",
                "mandatory content exceeds the available input budget; compaction cannot reduce it",
            )
        if not isinstance(result, CompactionRequired):
            raise StoreError(
                "COMPACTION_NOT_POSSIBLE",
                "the transcript has no replaceable assistant/tool groups to compact yet",
            )
        before_estimated_tokens = result.estimated_tokens
        initiated = await self._mutate(
            session_id,
            lambda: self._store.begin_session_compaction(
                session_id,
                client_command_id,
                payload_hash,
                before_estimated_tokens=before_estimated_tokens,
            ),
        )
        if initiated is None:
            return self._store.load_snapshot(session_id).session
        compaction = await self._compactor.compact(
            result.plan,
            cancellation,
            invoke=self._invoke_maintenance_compaction,
        )
        after_estimated_tokens = before_estimated_tokens
        error_code: str | None = None
        if compaction.error is not None:
            error_code = compaction.error.code
        else:
            rebuilt = self._context_builder.build(
                self._store.load_committed_transcript(session_id),
                compaction.snapshot,
                base_request,
            )
            after_estimated_tokens = (
                rebuilt.required_tokens
                if isinstance(rebuilt, ContextOverflow)
                else rebuilt.estimated_tokens
            )
        await self._mutate(
            session_id,
            lambda: self._store.finish_session_compaction(
                session_id,
                before_estimated_tokens=before_estimated_tokens,
                after_estimated_tokens=after_estimated_tokens,
                error_code=error_code,
            ),
        )
        return self._store.load_snapshot(session_id).session

    async def _invoke_maintenance_compaction(
        self, request: ModelRequest, cancellation: CancellationToken
    ) -> AssistantTurn:
        """Serve a run-less compaction request with the run loop's retry policy."""

        async def operation() -> AssistantTurn:
            return await self._model.complete(request, _ignore_delta, cancellation)

        async def on_retry(_: RetryNotice) -> None:
            return None

        return await self._invoker.invoke(operation, cancellation, on_retry)

    def _workspace_boundary(self, session_id: str) -> WorkspaceBoundary:
        return WorkspaceBoundary(
            Path(self._store.load_snapshot(session_id).session.workspace_realpath)
        )

    def _session_fingerprints(self, session_id: str) -> dict[str, str]:
        """Return the live per-session read/write fingerprint view for the tools."""
        return self._content_fingerprints.setdefault(session_id, {})

    def _record_content_fingerprint(
        self, session_id: str, prepared: PreparedToolCall, result: ToolResult
    ) -> None:
        """Track the freshest content the session has seen for the freshness gate.

        read_file reports the hash of the file's full bytes and write_file the hash
        of what it wrote, so a later write in the same session can prove it targets
        content the model has already seen in its current form.
        """
        sha256 = result.data.get("sha256")
        if prepared.target and isinstance(sha256, str):
            self._session_fingerprints(session_id)[prepared.target] = sha256

    def _scan_workspace(self, session_id: str) -> WorkspaceScan:
        """Freeze the workspace's instructions and skill index for this run.

        The scan happens at run start, not session creation, so edits between runs
        take effect on the next run; within a run the system prompt stays stable.
        """
        return scan_workspace(self._workspace_boundary(session_id))

    def _compiled_system(self, session_id: str, scan: WorkspaceScan) -> str:
        """Compile the developer instructions with the current environment.

        Spec 7.2 and 7.3 item 1 make environment information mandatory in every model
        view, so it belongs in the system string the context estimator measures. Only
        facts this process already owns appear here: the session workspace root, the
        platform and the configured tool limits, never a credential or its value.
        The workspace scan's AGENTS.md section and skill index ride along inside the
        same mandatory system content (spec 7.2 as amended, 10.5).
        """
        tools = self._settings.tools
        workspace_root = self._store.load_snapshot(session_id).session.workspace_realpath
        system = "\n".join(
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
        sections = render_workspace_sections(scan)
        return f"{system}\n\n{sections}" if sections else system

    async def _build_context(
        self,
        run_id: str,
        session_id: str,
        cancellation: CancellationToken,
        *,
        round_no: int,
        scan: WorkspaceScan,
        force_compaction: bool = False,
    ) -> ReadyContext | RunOutcome:
        base_request = self._context_request(session_id, scan, current_run_id=run_id)
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
            before_estimated_tokens = result.estimated_tokens
            await self._mutate(
                session_id,
                lambda: self._store.record_diagnostic(
                    run_id,
                    "compaction.started",
                    {
                        "before_estimated_tokens": before_estimated_tokens,
                        "forced": force_compaction,
                    },
                ),
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
            await self._mutate(
                session_id,
                lambda: self._store.record_diagnostic(
                    run_id,
                    "compaction.finished",
                    {
                        "before_estimated_tokens": before_estimated_tokens,
                        "after_estimated_tokens": rebuilt.estimated_tokens,
                        "forced": force_compaction,
                    },
                ),
            )
            self._record_context_estimate(run_id, rebuilt)
            return rebuilt
        self._record_context_estimate(run_id, result)
        return result

    def _context_request(
        self,
        session_id: str,
        scan: WorkspaceScan,
        *,
        current_run_id: str | None,
    ) -> ContextRequest:
        """Assemble the budget request every build — run and maintenance alike — shares."""
        return ContextRequest(
            system=self._compiled_system(session_id, scan),
            context_window=self._settings.model.context_window,
            max_output_tokens=self._settings.model.max_output_tokens,
            safety_margin_tokens=self._settings.context.safety_margin_tokens,
            compact_trigger_ratio=self._settings.context.compact_trigger_ratio,
            compact_target_ratio=self._settings.context.compact_target_ratio,
            summary_max_tokens=self._settings.context.summary_max_tokens,
            recent_user_turns=self._settings.context.recent_turns_min,
            current_run_id=current_run_id,
            tool_schemas=tuple(self._tools.schemas()),
        )

    def _record_context_estimate(self, run_id: str, ready: ReadyContext) -> None:
        """Publish the build's estimate as the run's read-only context projection."""
        self._store.record_context_estimate(
            run_id,
            RunContextEstimate(
                estimated_tokens=ready.estimated_tokens,
                available_tokens=ready.available_tokens,
                window_tokens=self._settings.model.context_window,
                max_output_tokens=self._settings.model.max_output_tokens,
                safety_margin_tokens=self._settings.context.safety_margin_tokens,
            ),
        )

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

            async def collect_thinking_delta(delta: ThinkingDelta) -> None:
                await self._publisher.publish_transient(
                    AssistantThinkingDelta(
                        session_id=session_id,
                        run_id=run_id,
                        draft_epoch=draft_epoch,
                        index=delta.index,
                        text=delta.text,
                    )
                )

            async def close_thinking_block(closed: ThinkingBlockClosed) -> None:
                await self._publisher.publish_transient(
                    AssistantThinkingClosed(
                        session_id=session_id,
                        run_id=run_id,
                        draft_epoch=draft_epoch,
                        index=closed.index,
                    )
                )

            try:
                return await self._model.complete(
                    request,
                    collect_delta,
                    cancellation,
                    on_thinking_delta=collect_thinking_delta,
                    on_thinking_block_closed=close_thinking_block,
                )
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


async def _ignore_delta(_: TextDelta) -> None:
    """Consume streamed text the maintenance compaction never displays."""


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
