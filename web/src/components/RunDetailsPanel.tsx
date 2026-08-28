import type { SessionSnapshotDto } from "../api/types";

type RunDetailsPanelProps = { snapshot: SessionSnapshotDto | null };

export function RunDetailsPanel({ snapshot }: RunDetailsPanelProps) {
  const run = snapshot?.active_run;
  if (run === null || run === undefined) {
    return <section><h2>Run details</h2><p>No active run.</p></section>;
  }
  return (
    <section>
      <h2>Run details</h2>
      <dl className="run-details-list">
        <div><dt>State</dt><dd>{run.state}</dd></div>
        <div><dt>Run ID</dt><dd>{run.id}</dd></div>
        <div><dt>Started</dt><dd>{new Date(run.started_at).toLocaleString()}</dd></div>
      </dl>
    </section>
  );
}
