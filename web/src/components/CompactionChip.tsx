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
      <p className="compaction-chip compaction-running" role="status">
        正在压缩上下文…
      </p>
    );
  }
  if (status.phase === "finished" && status.errorCode !== undefined) {
    return (
      <p className="compaction-chip compaction-finished" role="status">
        压缩失败（{status.errorCode}）：已保留原上下文（约{" "}
        {tokenCount(status.beforeTokens)} tokens），可稍后重试 /compact
      </p>
    );
  }
  return (
    <p className="compaction-chip compaction-finished" role="status">
      上下文已压缩：{tokenCount(status.beforeTokens)} →{" "}
      {tokenCount(status.afterTokens)} tokens
    </p>
  );
}
