import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";

import { menuPop } from "../motion";
import type { RunDto } from "../api/types";
import { IconSend, IconStop } from "./icons";

const TERMINAL_RUN_STATES = new Set([
  "completed",
  "stopped",
  "cancelled",
  "failed",
  "interrupted",
]);

const SLASH_COMMANDS = [
  { name: "/clear", description: "清空当前会话" },
  { name: "/compact", description: "压缩上下文" },
];

export type ComposerProps = {
  activeRun: RunDto | null;
  draft: string;
  /** Persisted per-session approval mode (spec 13.4); the toggle is its only writer. */
  autoApprove: boolean;
  isRecoveryBlocked?: boolean;
  onDraftChange(value: string): void;
  onSend(content: string): void;
  onStop(runId: string): void;
  onApprovalModeChange(autoApprove: boolean): void;
  /** Clears the recovery gate; the ack lives here too, not only in the timeline banner. */
  onAcknowledgeRecovery?(): void;
};

/**
 * The slash menu is open while the draft is a single word starting with `/`;
 * picking an entry sends the command directly instead of a normal message.
 */
function slashMatches(draft: string) {
  if (!draft.startsWith("/") || draft.includes(" ")) return [];
  const query = draft.toLowerCase();
  return SLASH_COMMANDS.filter((command) => command.name.startsWith(query));
}

export function Composer({
  activeRun,
  draft,
  autoApprove,
  isRecoveryBlocked = false,
  onDraftChange,
  onSend,
  onStop,
  onApprovalModeChange,
  onAcknowledgeRecovery,
}: ComposerProps) {
  const [isFocused, setIsFocused] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const menuRef = useRef<HTMLUListElement | null>(null);
  const active =
    activeRun !== null && !TERMINAL_RUN_STATES.has(activeRun.state);
  const isCancelling = activeRun?.state === "cancelling";
  const canSend = draft.trim().length > 0 && !isRecoveryBlocked;
  const matches = slashMatches(draft);
  const menuOpen = isFocused && matches.length > 0 && !isRecoveryBlocked;

  useEffect(() => {
    setHighlighted(0);
  }, [draft]);

  function pick(index: number) {
    const command = matches[index];
    if (command === undefined) return;
    onSend(command.name);
    onDraftChange("");
  }

  return (
    <form
      className={`composer${isFocused ? " composer-focused" : ""}`}
      aria-label="消息输入框"
      onFocus={() => setIsFocused(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget))
          setIsFocused(false);
      }}
      onSubmit={(event) => {
        event.preventDefault();
        if (!active && canSend) onSend(draft.trim());
      }}
    >
      <label className="sr-only" htmlFor="composer-message">
        消息
      </label>
      <textarea
        id="composer-message"
        aria-label="消息"
        aria-describedby={
          isRecoveryBlocked ? "composer-recovery-note" : undefined
        }
        value={draft}
        disabled={isRecoveryBlocked}
        placeholder={
          isRecoveryBlocked
            ? "请先确认已检查工作区/进程，再发送消息"
            : "给 Make Code Great Again 发消息，或输入 / 使用命令"
        }
        onChange={(event) => onDraftChange(event.target.value)}
        onKeyDown={(event) => {
          if (!menuOpen) return;
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setHighlighted((current) => (current + 1) % matches.length);
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setHighlighted((current) =>
              current === 0 ? matches.length - 1 : current - 1,
            );
          } else if (event.key === "Tab" && matches.length === 1) {
            event.preventDefault();
            onDraftChange(matches[0].name);
          } else if (event.key === "Enter" || event.key === "Escape") {
            event.preventDefault();
            if (event.key === "Enter") pick(highlighted);
          }
        }}
      />
      {menuOpen ? (
        <motion.ul
          className="slash-menu"
          role="listbox"
          aria-label="斜杠命令"
          ref={menuRef}
          variants={menuPop}
          initial="initial"
          animate="animate"
        >
          {matches.map((command, index) => (
            <li key={command.name}>
              <button
                type="button"
                role="option"
                aria-selected={index === highlighted}
                className={
                  index === highlighted
                    ? "slash-item slash-item-active"
                    : "slash-item"
                }
                onMouseEnter={() => setHighlighted(index)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => pick(index)}
              >
                <span className="slash-name">{command.name}</span>
                <span className="slash-desc">{command.description}</span>
              </button>
            </li>
          ))}
        </motion.ul>
      ) : null}
      {isRecoveryBlocked ? (
        <p className="composer-note" id="composer-recovery-note">
          输入框已禁用：请先确认已检查工作区/进程。
        </p>
      ) : null}
      <div className="composer-actions">
        {isRecoveryBlocked && onAcknowledgeRecovery !== undefined ? (
          <button
            type="button"
            className="composer-recovery-ack"
            onClick={onAcknowledgeRecovery}
          >
            我已检查，继续对话
          </button>
        ) : null}
        <button
          type="button"
          className={`approval-mode-btn${autoApprove ? " approval-mode-on" : ""}`}
          aria-pressed={autoApprove}
          title={
            autoApprove
              ? "当前为自动批准，点击恢复人工审批"
              : "当前为人工审批，点击切换自动批准"
          }
          onClick={() => onApprovalModeChange(!autoApprove)}
        >
          {/* The label names the mode the session is IN right now — the persisted
              server state the prop carries — so the display can never disagree
              with what the approval gate actually enforces. */}
          {autoApprove ? "自动批准" : "人工审批"}
        </button>
        {active ? (
          <button
            className="composer-stop"
            type="button"
            disabled={isCancelling}
            onClick={() => {
              if (activeRun !== null && !isCancelling) onStop(activeRun.id);
            }}
          >
            <IconStop />
            {isCancelling ? "正在停止" : "停止"}
          </button>
        ) : (
          <button className="composer-send" type="submit" disabled={!canSend}>
            <IconSend />
            发送
          </button>
        )}
      </div>
    </form>
  );
}
