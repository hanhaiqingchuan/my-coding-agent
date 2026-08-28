import type { JsonValue, RunTotalsDto, SessionSnapshotDto } from "../api/types";

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
  const model = modelName(run.config_snapshot.model);
  const stopReason = run.stop_reason?.replaceAll("_", " ") ?? null;
  const errorKind = run.error_kind?.replaceAll("_", " ") ?? null;
  return (
    <section>
      <h2>Run details</h2>
      <dl className="run-details-list">
        <div>
          <dt>State</dt>
          <dd className={`run-state run-state-${run.state}`}>{run.state}</dd>
        </div>
        <div>
          <dt>Run ID</dt>
          <dd className="run-details-mono">{run.id}</dd>
        </div>
        {model !== null ? <DetailRow label="Model" value={model} /> : null}
        <DetailRow label="Rounds" value={String(run.totals.round_count)} />
        <DetailRow label="Retries" value={String(run.totals.retry_count)} />
        <div>
          <dt>Cumulative tokens</dt>
          <dd>
            <span>{tokenSummary(run.totals)}</span>
            <span className="run-details-note">
              Known usage summed across rounds, not current context occupancy.
            </span>
          </dd>
        </div>
        {stopReason !== null ? (
          <DetailRow label="Stop reason" value={stopReason} />
        ) : null}
        {errorKind !== null ? (
          <DetailRow label="Error kind" value={errorKind} />
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

function tokenSummary(totals: RunTotalsDto): string {
  return [
    `input ${totals.input_tokens}`,
    `output ${totals.output_tokens}`,
    `cache create ${totals.cache_creation_input_tokens}`,
    `cache read ${totals.cache_read_input_tokens}`,
  ].join(" · ");
}

/**
 * The run's configuration snapshot is the backend's `asdict(AppSettings)`, so the model
 * identity only exists as `config_snapshot.model.model`. Anything else stays unrendered
 * rather than being guessed at from another key.
 */
function modelName(value: JsonValue | undefined): string | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const name = value.model;
  return typeof name === "string" && name.length > 0 ? name : null;
}
