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
  return rate === null ? "—" : `${(rate * 100).toFixed(1)}%`;
}

function decimal(value: number | null): string {
  return value === null ? "—" : value.toFixed(1);
}

function seconds(ms: number | null): string {
  return ms === null ? "—" : `${(ms / 1000).toFixed(1)}s`;
}

function tokens(value: number | null): string {
  return value === null ? "—" : Math.round(value).toLocaleString("en-US");
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
      label: "裁判错误",
      content: "裁判错误",
      className: "eval-badge eval-fail",
    };
  }
  if (row.judge_scores === null) {
    return {
      label: "裁判评分：未评判",
      content: "—",
      className: "eval-muted",
    };
  }
  const scores = SCORE_NAMES.map(
    (name) => `${SCORE_LABELS[name]} ${row.judge_scores?.[name] ?? "—"}`,
  ).join("，");
  const content = SCORE_NAMES.map((name) =>
    String(row.judge_scores?.[name] ?? "—"),
  ).join(" · ");
  return {
    label: `裁判评分：${scores}`,
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
          <span className="eval-badge eval-fail">已损坏</span>{" "}
          {summary.note ?? ""}
        </p>
      ) : summary.note !== null ? (
        <p className="eval-note">{summary.note}</p>
      ) : null}
      {aggregates !== null ? (
        <div className="eval-aggregates">
          <div>
            <span>严格成功</span>
            <strong>{percent(aggregates.task_completion_rate)}</strong>
            <p>
              {aggregates.valid_runs} 次有效运行中通过{" "}
              {aggregates.strict_success_runs}
            </p>
          </div>
          <div>
            <span>成本</span>
            <p>
              Token 输入 {aggregates.total_input_tokens ?? "—"} · 输出{" "}
              {aggregates.total_output_tokens ?? "—"}
            </p>
            <p>
              主请求 {aggregates.total_main_requests} · 工具调用{" "}
              {aggregates.total_tool_calls}
            </p>
            <p>
              平均每次运行 输入 {tokens(summary.avg_input_tokens)} · 输出{" "}
              {tokens(summary.avg_output_tokens)}
            </p>
          </div>
          <div>
            <span>平均轮次</span>
            <strong>{decimal(summary.avg_rounds)}</strong>
            <p>每次运行的模型调用轮数</p>
          </div>
          <div>
            <span>平均工具</span>
            <strong>{decimal(summary.avg_tool_calls)}</strong>
            <p>执行数 · 失败 {decimal(summary.avg_tool_failures)}</p>
          </div>
          <div>
            <span>平均耗时</span>
            <strong>{seconds(summary.avg_duration_ms)}</strong>
            <p>每次运行的智能体工作时长</p>
          </div>
          <div>
            <span>裁判</span>
            <strong>{means(aggregates.judge_means)}</strong>
            <p>
              已评判 {aggregates.judged_runs} · 裁判错误{" "}
              {aggregates.judge_error_runs}
            </p>
          </div>
          {Object.keys(aggregates.failure_kinds).length > 0 ? (
            <div>
              <span>失败分布</span>
              <strong>
                {Object.entries(aggregates.failure_kinds)
                  .map(([kind, times]) => `${kind} ×${times}`)
                  .join("，")}
              </strong>
            </div>
          ) : null}
        </div>
      ) : null}
      {detail.tasks.length > 0 ? (
        <table className="eval-task-table">
          <thead>
            <tr>
              <th scope="col">任务</th>
              <th scope="col">分类</th>
              <th scope="col">状态</th>
              <th scope="col">停止原因</th>
              <th scope="col">轮次</th>
              <th scope="col">工具</th>
              <th scope="col">输入</th>
              <th scope="col">输出</th>
              <th scope="col">耗时</th>
              <th scope="col">严格</th>
              <th scope="col">产物</th>
              <th scope="col">裁判</th>
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
                        <small>· 第 {row.repeat} 次</small>
                      </button>
                    </td>
                    <td>{task.category}</td>
                    <td aria-label="状态">{row.state ?? "—"}</td>
                    <td aria-label="停止原因">{row.stop_reason ?? "—"}</td>
                    <td aria-label="轮次">{row.rounds ?? "—"}</td>
                    <td aria-label="工具调用">{row.tool_calls ?? "—"}</td>
                    <td aria-label="输入 Token">{row.input_tokens ?? "—"}</td>
                    <td aria-label="输出 Token">{row.output_tokens ?? "—"}</td>
                    <td aria-label="耗时">{duration(row)}</td>
                    <td>
                      <span
                        className={`eval-badge ${row.strict_success ? "eval-pass" : "eval-fail"}`}
                        aria-label={`严格成功：${row.strict_success ? "通过" : "未通过"}`}
                      >
                        {row.strict_success ? "通过" : "未通过"}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`eval-badge ${row.artifact_correct ? "eval-pass" : "eval-fail"}`}
                        aria-label={`产物正确：${row.artifact_correct ? "正确" : "错误"}`}
                      >
                        {row.artifact_correct ? "正确" : "错误"}
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
        <p className="eval-note">该轮次没有可读取的运行记录。</p>
      ) : null}
    </div>
  );
}
