import { JudgementCard } from "./JudgementCard";
import type { RunDetailDto } from "./types";

type RunDetailProps = {
  detail: RunDetailDto;
};

function text(value: string | null | undefined): string {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function count(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : String(value);
}

function ms(value: number | null | undefined): string {
  return value === null || value === undefined
    ? "—"
    : `${value.toLocaleString("en-US")} ms`;
}

function oracle(outcome: {
  passed: boolean | null;
  exit_code: number | null;
  errored: boolean;
}): string {
  if (outcome.errored) {
    return "errored";
  }
  if (outcome.passed === null) {
    return "—";
  }
  return outcome.passed ? "passed" : "failed";
}

/** One run's verbatim run-v1 facts plus its judgement, read-only. */
export function RunDetail({ detail }: RunDetailProps) {
  const run = detail.run;
  const finalMessage = run?.agent_report.final_assistant_text;
  return (
    <div className="eval-run-detail">
      <header className="eval-run-heading">
        <p>
          Run in {detail.campaign} · {text(detail.run?.category)}
        </p>
        <h2>
          {detail.task_id} · repeat {detail.repeat}
        </h2>
      </header>
      {run === null ? (
        <div className="eval-warning" role="alert">
          {detail.run_note ?? "The run record is unreadable."}
        </div>
      ) : (
        <>
          <dl className="run-details-list">
            <div>
              <dt>State</dt>
              <dd>{text(run.state)}</dd>
            </div>
            <div>
              <dt>Stop reason</dt>
              <dd>{text(run.stop_reason)}</dd>
            </div>
            <div>
              <dt>Outcome</dt>
              <dd>{run.outcome}</dd>
            </div>
            <div>
              <dt>Failure</dt>
              <dd>
                {run.failure_kind === null
                  ? "—"
                  : `${run.failure_stage ?? "—"} · ${run.failure_kind}`}
              </dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>{text(run.model_identity?.name)}</dd>
            </div>
            <div>
              <dt>Agent commit</dt>
              <dd className="run-details-mono">{text(run.agent_commit)}</dd>
            </div>
            <div>
              <dt>Rounds</dt>
              <dd>{count(run.model.main_requests)}</dd>
            </div>
            <div>
              <dt>Attempts</dt>
              <dd>{count(run.model.attempts)}</dd>
            </div>
            <div>
              <dt>Network retries</dt>
              <dd>{count(run.model.network_retries)}</dd>
            </div>
            <div>
              <dt>Input tokens</dt>
              <dd>{count(run.model.usage.input_tokens)}</dd>
            </div>
            <div>
              <dt>Output tokens</dt>
              <dd>{count(run.model.usage.output_tokens)}</dd>
            </div>
            <div>
              <dt>Cache tokens</dt>
              <dd>
                {count(run.model.usage.cache_creation_input_tokens)} created ·{" "}
                {count(run.model.usage.cache_read_input_tokens)} read
              </dd>
            </div>
            <div>
              <dt>Tool calls</dt>
              <dd>
                {run.tools.executed} of {run.tools.proposed} proposed
              </dd>
            </div>
            <div>
              <dt>Failed tool calls</dt>
              <dd>{run.tools.failed}</dd>
            </div>
            <div>
              <dt>Compactions</dt>
              <dd>{run.compaction.count}</dd>
            </div>
            <div>
              <dt>Target oracle</dt>
              <dd>{oracle(run.oracle.target)}</dd>
            </div>
            <div>
              <dt>Regression oracle</dt>
              <dd>{oracle(run.oracle.regression)}</dd>
            </div>
            <div>
              <dt>Files</dt>
              <dd>
                {run.modifications.files_added} added ·{" "}
                {run.modifications.files_modified} modified ·{" "}
                {run.modifications.files_deleted} deleted
              </dd>
            </div>
            <div>
              <dt>Lines</dt>
              <dd>
                +{run.modifications.lines_added} / −
                {run.modifications.lines_removed}
              </dd>
            </div>
            <div>
              <dt>Agent duration</dt>
              <dd>
                {ms(
                  run.durations.agent_monotonic_ms ??
                    run.durations.agent_process_ms,
                )}
              </dd>
            </div>
            <div>
              <dt>Total duration</dt>
              <dd>{ms(run.durations.total_ms)}</dd>
            </div>
          </dl>
          {typeof finalMessage === "string" && finalMessage !== "" ? (
            <section
              className="eval-final-message"
              aria-label="Final assistant message"
            >
              <h3>Final assistant message</h3>
              <p>{finalMessage}</p>
            </section>
          ) : null}
        </>
      )}
      <JudgementCard
        judgement={detail.judgement}
        note={detail.judgement_note}
      />
    </div>
  );
}
