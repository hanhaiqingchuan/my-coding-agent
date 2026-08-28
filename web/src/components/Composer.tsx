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
  isRecoveryBlocked?: boolean;
  onDraftChange(value: string): void;
  onSend(content: string): void;
  onStop(runId: string): void;
};

export function Composer({
  activeRun,
  draft,
  isRecoveryBlocked = false,
  onDraftChange,
  onSend,
  onStop,
}: ComposerProps) {
  const [isFocused, setIsFocused] = useState(false);
  const active =
    activeRun !== null && !TERMINAL_RUN_STATES.has(activeRun.state);
  const isCancelling = activeRun?.state === "cancelling";
  const canSend = draft.trim().length > 0 && !isRecoveryBlocked;

  return (
    <form
      className={`composer${isFocused ? " composer-focused" : ""}`}
      aria-label="Message composer"
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
        Message
      </label>
      <textarea
        id="composer-message"
        aria-label="Message"
        value={draft}
        placeholder={
          isRecoveryBlocked
            ? "Confirm recovery before sending a message"
            : "Message the agent"
        }
        onChange={(event) => onDraftChange(event.target.value)}
      />
      <div className="composer-actions">
        {active ? (
          <button
            className="composer-stop"
            type="button"
            disabled={isCancelling}
            onClick={() => {
              if (activeRun !== null && !isCancelling) onStop(activeRun.id);
            }}
          >
            {isCancelling ? "正在停止" : "Stop"}
          </button>
        ) : (
          <button className="composer-send" type="submit" disabled={!canSend}>
            Send
          </button>
        )}
      </div>
    </form>
  );
}
