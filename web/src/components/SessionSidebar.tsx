import { motion } from "motion/react";

import { staggerChild, staggerParent } from "../motion";
import type { SessionDto } from "../api/types";
import { IconX } from "./icons";

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
      <motion.ul
        className="session-list"
        variants={staggerParent}
        initial="initial"
        animate="animate"
      >
        {sessions.map((session) => (
          <motion.li
            key={session.id}
            className="session-item"
            variants={staggerChild}
          >
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
              <IconX />
            </button>
          </motion.li>
        ))}
      </motion.ul>
    </section>
  );
}
