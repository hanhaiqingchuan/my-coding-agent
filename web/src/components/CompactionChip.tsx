import { motion } from "motion/react";

import { riseIn } from "../motion";
import type { CompactionStatus } from "../features/sessions/sessionReducer";

type CompactionChipProps = { status: CompactionStatus | null };

function tokenCount(value: number): string {
  return value.toLocaleString();
}

/**
 * Compaction visibility next to the composer: `running` while the compactor works,
 * and the before/after estimates once it finishes. Both run-internal auto-compaction
 * and the `/compact` maintenance command emit the same durable events, so this chip
 * covers the two paths without inspecting the run state.
 */
export function CompactionChip({ status }: CompactionChipProps) {
  if (status === null) return null;
  if (status.phase === "running") {
    return (
      <motion.p
        className="compaction-chip compaction-running"
        role="status"
        variants={riseIn}
        initial="initial"
        animate="animate"
      >
        正在压缩上下文…
      </motion.p>
    );
  }
  if (status.phase === "finished" && status.errorCode !== undefined) {
    return (
      <motion.p
        className="compaction-chip compaction-finished"
        role="status"
        variants={riseIn}
        initial="initial"
        animate="animate"
      >
        压缩失败（{status.errorCode}）：已保留原上下文（约{" "}
        {tokenCount(status.beforeTokens)} tokens），可稍后重试 /compact
      </motion.p>
    );
  }
  return (
    <motion.p
      className="compaction-chip compaction-finished"
      role="status"
      variants={riseIn}
      initial="initial"
      animate="animate"
    >
      上下文已压缩：{tokenCount(status.beforeTokens)} →{" "}
      {tokenCount(status.afterTokens)} tokens
    </motion.p>
  );
}
