import type { SessionDto } from "../api/types";

type SessionSidebarProps = {
  sessions: SessionDto[];
  selectedSessionId: string | null;
  onSelect(sessionId: string): void;
};

export function SessionSidebar({
  sessions,
  selectedSessionId,
  onSelect,
}: SessionSidebarProps) {
  return (
    <section aria-labelledby="sessions-heading">
      <h2 id="sessions-heading">会话</h2>
      <ul className="session-list">
        {sessions.map((session) => (
          <li key={session.id}>
            <button
              type="button"
              className="session-row"
              aria-current={
                session.id === selectedSessionId ? "page" : undefined
              }
              onClick={() => onSelect(session.id)}
            >
              <span>{session.title ?? "未命名会话"}</span>
              <small>{session.workspace_realpath}</small>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
