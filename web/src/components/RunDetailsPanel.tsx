import type { SessionSnapshotDto } from "../api/types";

type RunDetailsPanelProps = { snapshot: SessionSnapshotDto | null };

export function RunDetailsPanel({ snapshot }: RunDetailsPanelProps) {
  const run = snapshot?.active_run;
  if (run === null || run === undefined) {
    return (
      <section>
        <h2>Run details</h2>
        <p>No active run.</p>
      </section>
    );
  }
  const config = run.config_snapshot;
  const model = printable(config.model ?? config.model_name);
  const round = printable(config.round ?? config.current_round);
  const contextUsed = printable(config.context_used ?? config.context_tokens);
  const contextWindow = printable(config.context_window);
  const retry = printable(config.retry_attempt ?? config.retry_count);
  const context =
    contextUsed !== null && contextWindow !== null
      ? `${contextUsed} / ${contextWindow}`
      : null;
  const stopReason = run.stop_reason?.replaceAll("_", " ") ?? null;
  return (
    <section>
      <h2>Run details</h2>
      <dl className="run-details-list">
        <div>
          <dt>State</dt>
          <dd>{run.state}</dd>
        </div>
        <div>
          <dt>Run ID</dt>
          <dd>{run.id}</dd>
        </div>
        {model !== null ? <DetailRow label="Model" value={model} /> : null}
        {round !== null ? <DetailRow label="Round" value={round} /> : null}
        {context !== null ? (
          <DetailRow label="Context" value={context} />
        ) : null}
        {retry !== null ? <DetailRow label="Retry" value={retry} /> : null}
        {stopReason !== null ? (
          <DetailRow label="Stop reason" value={stopReason} />
        ) : null}
        <div>
          <dt>Started</dt>
          <dd>{new Date(run.started_at).toLocaleString()}</dd>
        </div>
      </dl>
    </section>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function printable(value: unknown): string | null {
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : null;
}
