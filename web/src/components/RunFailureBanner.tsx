import { useState } from "react";
import { motion } from "motion/react";

import { riseIn } from "../motion";
import type { RunFailure } from "../api/errors";
import { IconX } from "./icons";

/**
 * The timeline's compact, dismissible summary of the last finished run's
 * failure. It complements the run-details panel: the panel keeps the facts,
 * the banner puts the cause and the next step where the conversation happens.
 * Dismissal is per run id, so a snapshot refresh never resurrects it but a new
 * failing run does.
 */
export function RunFailureBanner({ failure }: { failure: RunFailure }) {
  const [dismissedRunId, setDismissedRunId] = useState<string | null>(null);
  if (failure.runId === dismissedRunId) return null;
  return (
    <motion.aside
      className="run-failure-banner"
      role="status"
      data-testid="run-failure-banner"
      variants={riseIn}
      initial="initial"
      animate="animate"
    >
      <div className="run-failure-copy">
        <strong>{failure.title}</strong>
        <p>{failure.description}</p>
        {failure.retryCount > 0 ? (
          <p className="run-failure-retries">
            已自动重试 {failure.retryCount} 次。
          </p>
        ) : null}
        {failure.hint !== null ? (
          <p className="run-failure-hint">{failure.hint}</p>
        ) : null}
      </div>
      <button
        type="button"
        className="run-failure-dismiss"
        aria-label="关闭提示"
        onClick={() => setDismissedRunId(failure.runId)}
      >
        <IconX />
      </button>
    </motion.aside>
  );
}
