import {
  SCORE_LABELS,
  SCORE_NAMES,
  type CampaignDetailDto,
  type RunRowDto,
} from "./types";

type EvaluationDetailProps = {
  detail: CampaignDetailDto;
  onOpenRun(taskId: string, repeat: number): void;
};

function percent(rate: number | null): string {
  return rate === null ? "n/a" : `${(rate * 100).toFixed(1)}%`;
}

function means(values: Record<string, number | null>): string {
  const rendered = SCORE_NAMES.map((name) => {
    const value = values[name];
    return value === null || value === undefined ? "—" : value.toFixed(1);
  });
  return rendered.join(" · ");
}

function duration(row: RunRowDto): string {
  const value = row.agent_ms ?? row.total_ms;
  return value === null ? "—" : `${value.toLocaleString("en-US")} ms`;
}

function judgeCell(row: RunRowDto): {
  label: string;
  content: string;
  className: string;
} {
  if (row.judge_error) {
    return {
      label: "Judge error",
      content: "judge error",
      className: "eval-badge eval-fail",
    };
  }
  if (row.judge_scores === null) {
    return {
      label: "Judge scores: not judged",
      content: "—",
      className: "eval-muted",
    };
  }
  const scores = SCORE_NAMES.map(
    (name) => `${SCORE_LABELS[name]} ${row.judge_scores?.[name] ?? "—"}`,
  ).join(", ");
  const content = SCORE_NAMES.map((name) =>
    String(row.judge_scores?.[name] ?? "—"),
  ).join(" · ");
  return {
    label: `Judge scores: ${scores}`,
    content,
    className: "eval-judge-scores",
  };
}

/** One campaign: aggregates strip plus one row per run, grouped under its task. */
export function EvaluationDetail({ detail, onOpenRun }: EvaluationDetailProps) {
  const aggregates = detail.aggregates;
  const summary = detail.summary;
  return (
    <div className="eval-campaign-detail">
      {summary.corrupt ? (
        <p className="eval-corrupt-line">
          <span className="eval-badge eval-fail">corrupt</span>{" "}
          {summary.note ?? ""}
        </p>
      ) : summary.note !== null ? (
        <p className="eval-note">{summary.note}</p>
      ) : null}
      {aggregates !== null ? (
        <div className="eval-aggregates">
          <div>
            <span>Strict success</span>
            <strong>{percent(aggregates.task_completion_rate)}</strong>
            <p>
              {aggregates.strict_success_runs} / {aggregates.valid_runs} valid
              runs
            </p>
          </div>
          <div>
            <span>Cost</span>
            <p>
              Tokens {aggregates.total_input_tokens ?? "—"} in ·{" "}
              {aggregates.total_output_tokens ?? "—"} out
            </p>
            <p>
              {aggregates.total_main_requests} main requests ·{" "}
              {aggregates.total_tool_calls} tool calls
            </p>
          </div>
          <div>
            <span>Judge</span>
            <strong>{means(aggregates.judge_means)}</strong>
            <p>
              {aggregates.judged_runs} judged · {aggregates.judge_error_runs}{" "}
              judge errors
            </p>
          </div>
          {Object.keys(aggregates.failure_kinds).length > 0 ? (
            <div>
              <span>Failures</span>
              <strong>
                {Object.entries(aggregates.failure_kinds)
                  .map(([kind, times]) => `${kind} ×${times}`)
                  .join(", ")}
              </strong>
            </div>
          ) : null}
        </div>
      ) : null}
      {detail.tasks.length > 0 ? (
        <table className="eval-task-table">
          <thead>
            <tr>
              <th scope="col">Task</th>
              <th scope="col">Category</th>
              <th scope="col">State</th>
              <th scope="col">Stop reason</th>
              <th scope="col">Rounds</th>
              <th scope="col">Tools</th>
              <th scope="col">In</th>
              <th scope="col">Out</th>
              <th scope="col">Duration</th>
              <th scope="col">Strict</th>
              <th scope="col">Artifact</th>
              <th scope="col">Judge</th>
            </tr>
          </thead>
          <tbody>
            {detail.tasks.flatMap((task) =>
              task.runs.map((row) => {
                const judge = judgeCell(row);
                return (
                  <tr key={`${task.task_id}-${row.repeat}`}>
                    <td>
                      <button
                        type="button"
                        className="eval-run-link"
                        onClick={() => onOpenRun(task.task_id, row.repeat)}
                      >
                        <span>{task.task_id}</span>
                        <small>· repeat {row.repeat}</small>
                      </button>
                    </td>
                    <td>{task.category}</td>
                    <td aria-label="State">{row.state ?? "—"}</td>
                    <td aria-label="Stop reason">{row.stop_reason ?? "—"}</td>
                    <td aria-label="Rounds">{row.rounds ?? "—"}</td>
                    <td aria-label="Tool calls">{row.tool_calls ?? "—"}</td>
                    <td aria-label="Input tokens">{row.input_tokens ?? "—"}</td>
                    <td aria-label="Output tokens">
                      {row.output_tokens ?? "—"}
                    </td>
                    <td aria-label="Duration">{duration(row)}</td>
                    <td>
                      <span
                        className={`eval-badge ${row.strict_success ? "eval-pass" : "eval-fail"}`}
                        aria-label={`Strict success: ${row.strict_success ? "pass" : "fail"}`}
                      >
                        {row.strict_success ? "pass" : "fail"}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`eval-badge ${row.artifact_correct ? "eval-pass" : "eval-fail"}`}
                        aria-label={`Artifact correct: ${row.artifact_correct ? "ok" : "wrong"}`}
                      >
                        {row.artifact_correct ? "ok" : "wrong"}
                      </span>
                    </td>
                    <td>
                      <span
                        className={judge.className}
                        aria-label={judge.label}
                      >
                        {judge.content}
                      </span>
                    </td>
                  </tr>
                );
              }),
            )}
          </tbody>
        </table>
      ) : summary.corrupt ? (
        <p className="eval-note">No readable run records in this campaign.</p>
      ) : null}
    </div>
  );
}
