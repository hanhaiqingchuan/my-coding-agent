import { useEffect, useMemo, useState } from "react";

import { ApiClient } from "./api/client";
import type { ApprovalDecision, SessionDto } from "./api/types";
import { connectionLabel } from "./api/labels";
import { ApprovalDock } from "./components/ApprovalDock";
import { AppShell } from "./components/AppShell";
import { Composer } from "./components/Composer";
import { CompactionChip } from "./components/CompactionChip";
import { ContextGauge } from "./components/ContextGauge";
import { ConversationTimeline } from "./components/ConversationTimeline";
import { RunDetailsPanel } from "./components/RunDetailsPanel";
import { SessionSidebar } from "./components/SessionSidebar";
import { ViewSwitcher, type AppView } from "./components/ViewSwitcher";
import { WorkspacePicker } from "./components/WorkspacePicker";
import { EvaluationsPanel } from "./features/evaluation/EvaluationsPanel";
import { EvaluationClient } from "./features/evaluation/evaluationApi";
import { useSession } from "./features/sessions/useSession";

type AppRoute = { view: AppView; campaign: string | null };

/**
 * The app's whole router: `#/evaluations` (optionally `#/evaluations/<campaign>`
 * to open one campaign) selects the evaluations view; any other hash — the
 * empty default included — is the sessions workbench. Hash-only, so deep links
 * survive refresh, back/forward and pasting without a router dependency.
 */
function routeFromHash(hash: string): AppRoute {
  const match = /^#\/evaluations(?:\/([^/]+))?\/?$/.exec(hash);
  return match === null
    ? { view: "sessions", campaign: null }
    : { view: "evaluations", campaign: match[1] ?? null };
}

function useHashRoute(): AppRoute {
  const [route, setRoute] = useState(() => routeFromHash(window.location.hash));
  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  return route;
}

export default function App() {
  const api = useMemo(() => new ApiClient(), []);
  const evaluations = useMemo(() => new EvaluationClient(), []);
  const route = useHashRoute();
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

  async function deleteSession(sessionId: string) {
    const target = sessions.find((session) => session.id === sessionId);
    const label = target?.title ?? "未命名会话";
    if (!window.confirm(`删除会话「${label}」？该会话的全部记录将被移除。`)) return;
    try {
      await api.deleteSession(sessionId);
    } catch {
      // A 409 means a run is active in that session; surface it without unselecting.
      window.alert("删除失败：会话中仍有正在进行的运行，请先停止。");
      return;
    }
    setSessions((current) => {
      const remaining = current.filter((session) => session.id !== sessionId);
      setSelectedSessionId((selected) =>
        selected === sessionId ? (remaining[0]?.id ?? null) : selected,
      );
      return remaining;
    });
  }

  function commandId(): string {
    return crypto.randomUUID();
  }

  /** The rail's tabs navigate by rewriting the hash; hashchange re-renders. */
  function switchView(next: AppView) {
    const target = next === "evaluations" ? "#/evaluations" : "";
    if (window.location.hash !== target) {
      window.location.hash = target;
    }
  }

  function startRun(content: string) {
    if (selectedSessionId === null) return;
    // `/compact` is the maintenance slash command: it asks the coordinator to
    // force-compaction the committed transcript instead of starting a run, and the
    // backend rejects it while a run is active (the composer already shows Stop then).
    if (content === "/compact") {
      send({
        type: "session.compact",
        client_command_id: commandId(),
        session_id: selectedSessionId,
        payload: {},
      });
      dispatch({ type: "draft.changed", draftText: "" });
      return;
    }
    // `/clear` wipes the conversation but keeps the session (and its workspace).
    if (content === "/clear") {
      send({
        type: "session.clear",
        client_command_id: commandId(),
        session_id: selectedSessionId,
        payload: {},
      });
      dispatch({ type: "draft.changed", draftText: "" });
      return;
    }
    send({
      type: "run.start",
      client_command_id: commandId(),
      session_id: selectedSessionId,
      payload: { content },
    });
    dispatch({ type: "draft.changed", draftText: "" });
  }

  /** The toggle is the only writer of the per-session approval mode (spec 13.4). */
  function setApprovalMode(autoApprove: boolean) {
    if (selectedSessionId === null) return;
    send({
      type: "session.set_approval_mode",
      client_command_id: commandId(),
      session_id: selectedSessionId,
      payload: { auto_approve: autoApprove },
    });
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

  if (route.view === "evaluations") {
    return (
      <div className="app-shell evaluations-shell">
        <nav className="session-sidebar" aria-label="会话与工作区">
          <ViewSwitcher view={route.view} onViewChange={switchView} />
        </nav>
        <main className="conversation-panel" aria-label="评测记录">
          <EvaluationsPanel
            key={route.campaign ?? "list"}
            reader={evaluations}
            initialCampaign={route.campaign}
          />
        </main>
      </div>
    );
  }

  return (
    <AppShell
      sidebar={
        <>
          <ViewSwitcher view={route.view} onViewChange={switchView} />
          <WorkspacePicker onCreate={createSession} />
          <SessionSidebar
            sessions={sessions}
            selectedSessionId={selectedSessionId}
            onSelect={setSelectedSessionId}
            onDelete={deleteSession}
          />
        </>
      }
      conversation={
        <div className="conversation-workbench">
          <header className="conversation-heading">
            <div>
              <p>会话</p>
              <h1>{snapshot?.session.title ?? "对话"}</h1>
            </div>
            <span
              className={`connection-status connection-${state.connection}`}
            >
              {connectionLabel(state.connection)}
            </span>
          </header>
          {snapshot === null ? (
            <section className="conversation-empty">
              <p>先打开一个工作区开始。</p>
            </section>
          ) : (
            <ConversationTimeline
              messages={snapshot.messages}
              tools={snapshot.tools}
              assistantDrafts={state.assistantDrafts}
              thinkingDrafts={state.thinkingDrafts}
              toolOutputDrafts={state.toolOutputDrafts}
              interruptedBanner={snapshot.interrupted_banner}
              lastFinishedRun={snapshot.last_finished_run}
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
              autoApprove={snapshot?.session.auto_approve ?? false}
              isRecoveryBlocked={recoveryBlocked}
              onDraftChange={(draftText) =>
                dispatch({ type: "draft.changed", draftText })
              }
              onSend={startRun}
              onStop={stopRun}
              onApprovalModeChange={setApprovalMode}
            />
            {/* The focus run is the active one, else the last finished one: the
                gauge keeps reporting the context the model actually saw. */}
            <div className="composer-status">
              <ContextGauge
                context={
                  (snapshot?.active_run ?? snapshot?.last_finished_run)?.context ??
                  null
                }
              />
              <CompactionChip status={state.compaction} />
            </div>
          </div>
        </div>
      }
      runDetails={<RunDetailsPanel snapshot={snapshot} />}
    />
  );
}
