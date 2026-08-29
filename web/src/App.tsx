import { useEffect, useMemo, useState } from "react";

import { ApiClient } from "./api/client";
import type { ApprovalDecision, SessionDto } from "./api/types";
import { ApprovalDock } from "./components/ApprovalDock";
import { AppShell } from "./components/AppShell";
import { Composer } from "./components/Composer";
import { ConversationTimeline } from "./components/ConversationTimeline";
import { RunDetailsPanel } from "./components/RunDetailsPanel";
import { SessionSidebar } from "./components/SessionSidebar";
import { ViewSwitcher, type AppView } from "./components/ViewSwitcher";
import { WorkspacePicker } from "./components/WorkspacePicker";
import { EvaluationsPanel } from "./features/evaluation/EvaluationsPanel";
import { EvaluationClient } from "./features/evaluation/evaluationApi";
import { useSession } from "./features/sessions/useSession";

export default function App() {
  const api = useMemo(() => new ApiClient(), []);
  const evaluations = useMemo(() => new EvaluationClient(), []);
  const [view, setView] = useState<AppView>("sessions");
  const [sessions, setSessions] = useState<SessionDto[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    null,
  );
  const { state, dispatch, send } = useSession(api, selectedSessionId);

  useEffect(() => {
    void api.listSessions().then((loaded) => {
      setSessions(loaded);
      setSelectedSessionId((current) => current ?? loaded[0]?.id ?? null);
    });
  }, [api]);

  async function createSession(workspace: string, title: string | null) {
    const session = await api.createSession(workspace, title);
    setSessions((current) => [session, ...current]);
    setSelectedSessionId(session.id);
  }

  function commandId(): string {
    return crypto.randomUUID();
  }

  function startRun(content: string) {
    if (selectedSessionId === null) return;
    send({
      type: "run.start",
      client_command_id: commandId(),
      session_id: selectedSessionId,
      payload: { content },
    });
    dispatch({ type: "draft.changed", draftText: "" });
  }

  function stopRun(runId: string) {
    if (selectedSessionId === null) return;
    send({
      type: "run.stop",
      client_command_id: commandId(),
      session_id: selectedSessionId,
      payload: { run_id: runId },
    });
  }

  function resolveApproval(toolCallId: string, decision: ApprovalDecision) {
    const pendingApproval = state.snapshot?.pending_approval;
    if (
      selectedSessionId === null ||
      pendingApproval === null ||
      pendingApproval === undefined
    )
      return;
    send({
      type: "approval.resolve",
      client_command_id: commandId(),
      session_id: selectedSessionId,
      payload: {
        run_id: pendingApproval.run_id,
        tool_call_id: toolCallId,
        decision,
      },
    });
  }

  function acknowledgeRecovery() {
    if (selectedSessionId === null) return;
    send({
      type: "session.ack_recovery",
      client_command_id: commandId(),
      session_id: selectedSessionId,
      payload: {},
    });
  }

  const snapshot = state.snapshot;
  const recoveryBlocked = snapshot?.session.requires_recovery_ack ?? false;

  if (view === "evaluations") {
    return (
      <div className="app-shell evaluations-shell">
        <nav className="session-sidebar" aria-label="Sessions and workspace">
          <ViewSwitcher view={view} onViewChange={setView} />
        </nav>
        <main className="conversation-panel" aria-label="Evaluations">
          <EvaluationsPanel reader={evaluations} />
        </main>
      </div>
    );
  }

  return (
    <AppShell
      sidebar={
        <>
          <ViewSwitcher view={view} onViewChange={setView} />
          <WorkspacePicker onCreate={createSession} />
          <SessionSidebar
            sessions={sessions}
            selectedSessionId={selectedSessionId}
            onSelect={setSelectedSessionId}
          />
        </>
      }
      conversation={
        <div className="conversation-workbench">
          <header className="conversation-heading">
            <div>
              <p>Session</p>
              <h1>{snapshot?.session.title ?? "Conversation"}</h1>
            </div>
            <span
              className={`connection-status connection-${state.connection}`}
            >
              {state.connection}
            </span>
          </header>
          {snapshot === null ? (
            <section className="conversation-empty">
              <p>Open a workspace to begin.</p>
            </section>
          ) : (
            <ConversationTimeline
              messages={snapshot.messages}
              tools={snapshot.tools}
              assistantDrafts={state.assistantDrafts}
              thinkingDrafts={state.thinkingDrafts}
              toolOutputDrafts={state.toolOutputDrafts}
              interruptedBanner={snapshot.interrupted_banner}
              onAcknowledgeRecovery={acknowledgeRecovery}
            />
          )}
          <div className="conversation-controls">
            <ApprovalDock
              pendingApproval={snapshot?.pending_approval ?? null}
              onResolve={resolveApproval}
            />
            <Composer
              activeRun={snapshot?.active_run ?? null}
              draft={state.draftText}
              isRecoveryBlocked={recoveryBlocked}
              onDraftChange={(draftText) =>
                dispatch({ type: "draft.changed", draftText })
              }
              onSend={startRun}
              onStop={stopRun}
            />
          </div>
        </div>
      }
      runDetails={<RunDetailsPanel snapshot={snapshot} />}
    />
  );
}
