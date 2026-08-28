import { useEffect, useMemo, useState } from "react";

import { ApiClient } from "./api/client";
import type { SessionDto } from "./api/types";
import { AppShell } from "./components/AppShell";
import { RunDetailsPanel } from "./components/RunDetailsPanel";
import { SessionSidebar } from "./components/SessionSidebar";
import { WorkspacePicker } from "./components/WorkspacePicker";
import { useSession } from "./features/sessions/useSession";

export default function App() {
  const api = useMemo(() => new ApiClient(), []);
  const [sessions, setSessions] = useState<SessionDto[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const { state } = useSession(api, selectedSessionId);

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

  return (
    <AppShell
      sidebar={
        <>
          <WorkspacePicker onCreate={createSession} />
          <SessionSidebar
            sessions={sessions}
            selectedSessionId={selectedSessionId}
            onSelect={setSelectedSessionId}
          />
        </>
      }
      conversation={
        <section className="conversation-placeholder">
          <h1>Conversation</h1>
          <p>{selectedSessionId ? "Session connected to the durable event stream." : "Open a workspace to begin."}</p>
          <p className="connection-status">Connection: {state.connection}</p>
        </section>
      }
      runDetails={<RunDetailsPanel snapshot={state.snapshot} />}
    />
  );
}
