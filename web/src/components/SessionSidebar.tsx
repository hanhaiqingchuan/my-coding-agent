import type { SessionDto } from "../api/types";

type SessionSidebarProps = {
  sessions: SessionDto[];
  selectedSessionId: string | null;
  onSelect(sessionId: string): void;
  onDelete(sessionId: string): void;
};

export function SessionSidebar({
  sessions,
  selectedSessionId,
  onSelect,
  onDelete,
}: SessionSidebarProps) {
  return (
    <section aria-labelledby="sessions-heading">
      <h2 id="sessions-heading">会话</h2>
      <ul className="session-list">
        {sessions.map((session) => (
          <li key={session.id} className="session-item">
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
            <button
              type="button"
              className="session-delete"
              aria-label={`删除会话 ${session.title ?? "未命名会话"}`}
              title="删除会话"
              onClick={() => onDelete(session.id)}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
