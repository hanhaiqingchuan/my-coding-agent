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
    return "出错";
  }
  if (outcome.passed === null) {
    return "—";
  }
  return outcome.passed ? "通过" : "未通过";
}

/** One run's verbatim run-v1 facts plus its judgement, read-only. */
export function RunDetail({ detail }: RunDetailProps) {
  const run = detail.run;
  const finalMessage = run?.agent_report.final_assistant_text;
  return (
    <div className="eval-run-detail">
      <header className="eval-run-heading">
        <p>
          {detail.campaign} 中的运行 · {text(detail.run?.category)}
        </p>
        <h2>
          {detail.task_id} · 第 {detail.repeat} 次
        </h2>
      </header>
      {run === null ? (
        <div className="eval-warning" role="alert">
          {detail.run_note ?? "该运行记录无法读取。"}
        </div>
      ) : (
        <>
          <dl className="run-details-list">
            <div>
              <dt>状态</dt>
              <dd>{text(run.state)}</dd>
            </div>
            <div>
              <dt>停止原因</dt>
              <dd>{text(run.stop_reason)}</dd>
            </div>
            <div>
              <dt>结果</dt>
              <dd>{run.outcome}</dd>
            </div>
            <div>
              <dt>失败</dt>
              <dd>
                {run.failure_kind === null
                  ? "—"
                  : `${run.failure_stage ?? "—"} · ${run.failure_kind}`}
              </dd>
            </div>
            <div>
              <dt>模型</dt>
              <dd>{text(run.model_identity?.name)}</dd>
            </div>
            <div>
              <dt>智能体提交</dt>
              <dd className="run-details-mono">{text(run.agent_commit)}</dd>
            </div>
            <div>
              <dt>轮次</dt>
              <dd>{count(run.model.main_requests)}</dd>
            </div>
            <div>
              <dt>尝试次数</dt>
              <dd>{count(run.model.attempts)}</dd>
            </div>
            <div>
              <dt>网络重试</dt>
              <dd>{count(run.model.network_retries)}</dd>
            </div>
            <div>
              <dt>输入 Token</dt>
              <dd>{count(run.model.usage.input_tokens)}</dd>
            </div>
            <div>
              <dt>输出 Token</dt>
              <dd>{count(run.model.usage.output_tokens)}</dd>
            </div>
            <div>
              <dt>缓存 Token</dt>
              <dd>
                写入 {count(run.model.usage.cache_creation_input_tokens)} · 读取{" "}
                {count(run.model.usage.cache_read_input_tokens)}
              </dd>
            </div>
            <div>
              <dt>工具调用</dt>
              <dd>
                已执行 {run.tools.executed} / 共提议 {run.tools.proposed}
              </dd>
            </div>
            <div>
              <dt>失败的工具调用</dt>
              <dd>{run.tools.failed}</dd>
            </div>
            <div>
              <dt>上下文压缩</dt>
              <dd>{run.compaction.count}</dd>
            </div>
            <div>
              <dt>目标校验</dt>
              <dd>{oracle(run.oracle.target)}</dd>
            </div>
            <div>
              <dt>回归校验</dt>
              <dd>{oracle(run.oracle.regression)}</dd>
            </div>
            <div>
              <dt>文件</dt>
              <dd>
                新增 {run.modifications.files_added} · 修改{" "}
                {run.modifications.files_modified} · 删除{" "}
                {run.modifications.files_deleted}
              </dd>
            </div>
            <div>
              <dt>行数</dt>
              <dd>
                +{run.modifications.lines_added} / −
                {run.modifications.lines_removed}
              </dd>
            </div>
            <div>
              <dt>智能体耗时</dt>
              <dd>
                {ms(
                  run.durations.agent_monotonic_ms ??
                    run.durations.agent_process_ms,
                )}
              </dd>
            </div>
            <div>
              <dt>总耗时</dt>
              <dd>{ms(run.durations.total_ms)}</dd>
            </div>
          </dl>
          {typeof finalMessage === "string" && finalMessage !== "" ? (
            <section
              className="eval-final-message"
              aria-label="最终助手消息"
            >
              <h3>最终助手消息</h3>
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
