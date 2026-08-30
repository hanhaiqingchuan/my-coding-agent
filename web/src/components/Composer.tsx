import { useState } from "react";

import type { RunDto } from "../api/types";

const TERMINAL_RUN_STATES = new Set([
  "completed",
  "stopped",
  "cancelled",
  "failed",
  "interrupted",
]);

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
};

export function Composer({
  activeRun,
  draft,
  autoApprove,
  isRecoveryBlocked = false,
  onDraftChange,
  onSend,
  onStop,
  onApprovalModeChange,
}: ComposerProps) {
  const [isFocused, setIsFocused] = useState(false);
  const active =
    activeRun !== null && !TERMINAL_RUN_STATES.has(activeRun.state);
  const isCancelling = activeRun?.state === "cancelling";
  const canSend = draft.trim().length > 0 && !isRecoveryBlocked;

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
            : "给智能体发消息"
        }
        onChange={(event) => onDraftChange(event.target.value)}
      />
      {isRecoveryBlocked ? (
        <p className="composer-note" id="composer-recovery-note">
          输入框已禁用：请先确认已检查工作区/进程。
        </p>
      ) : null}
      <div className="composer-actions">
        <label
          className={`approval-mode${autoApprove ? " approval-mode-on" : ""}`}
        >
          <input
            type="checkbox"
            role="switch"
            checked={autoApprove}
            onChange={(event) => onApprovalModeChange(event.target.checked)}
          />
          自动批准
        </label>
        {active ? (
          <button
            className="composer-stop"
            type="button"
            disabled={isCancelling}
            onClick={() => {
              if (activeRun !== null && !isCancelling) onStop(activeRun.id);
            }}
          >
            {isCancelling ? "正在停止" : "停止"}
          </button>
        ) : (
          <button className="composer-send" type="submit" disabled={!canSend}>
            发送
          </button>
        )}
      </div>
    </form>
  );
}
