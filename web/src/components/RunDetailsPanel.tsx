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
        <div>
          <dt>Model</dt>
          <dd>{model}</dd>
        </div>
        <div>
          <dt>Round</dt>
          <dd>{round}</dd>
        </div>
        <div>
          <dt>Context</dt>
          <dd>
            {contextUsed === "—" ? "—" : `${contextUsed} / ${contextWindow}`}
          </dd>
        </div>
        <div>
          <dt>Retry</dt>
          <dd>{retry}</dd>
        </div>
        <div>
          <dt>Stop reason</dt>
          <dd>{run.stop_reason?.replaceAll("_", " ") ?? "—"}</dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd>{new Date(run.started_at).toLocaleString()}</dd>
        </div>
      </dl>
    </section>
  );
}

function printable(value: unknown): string {
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : "—";
}
