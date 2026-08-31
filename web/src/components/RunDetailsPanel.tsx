import { Fragment } from "react";

import { describeStopOutcome } from "../api/errors";
import { runStateLabel } from "../api/labels";
import type {
  ContextLoadDto,
  JsonValue,
  SessionSnapshotDto,
  SessionTotalsDto,
} from "../api/types";

type RunDetailsPanelProps = { snapshot: SessionSnapshotDto | null };

export function RunDetailsPanel({ snapshot }: RunDetailsPanelProps) {
  const activeRun = snapshot?.active_run ?? null;
  /**
   * `active_run` is strictly non-terminal and every stop reason is written in the
   * statement that makes a run terminal, so the reason a run ended only ever reaches
   * this panel through `last_finished_run`.
   */
  const finishedRun = snapshot?.last_finished_run ?? null;
  const run = activeRun ?? finishedRun;
  const sessionTotals = snapshot?.session_totals ?? null;
  const contextLoad = snapshot?.context_load ?? null;
  // Before the session's first run there is nothing to report at all: no run
  // identity, no counters, no context. Afterwards the counters below are all
  // session-scoped, so a new request never reads as the numbers being wiped.
  if (run === null && sessionTotals === null && contextLoad === null) {
    return (
      <section>
        <h2>运行详情</h2>
        <p>当前没有运行中的任务。</p>
      </section>
    );
  }
  const isFinished = activeRun === null;
  const model = run !== null ? modelName(run.config_snapshot.model) : null;
  const stopOutcome =
    run !== null ? describeStopOutcome(run.stop_reason, run.error_kind) : null;
  const errorOutcome =
    run !== null && run.error_kind !== null
      ? describeStopOutcome(run.error_kind)
      : null;
  return (
    <section>
      <h2>运行详情</h2>
      <p className="run-details-scope">
        {run === null
          ? "会话累计。当前没有运行中的任务。"
          : isFinished
            ? "上一个完成的任务。当前没有运行中的任务，计数为会话累计。"
            : "运行中的任务，计数为会话累计。"}
      </p>
      <dl className="run-details-list">
        {run !== null ? (
          <Fragment>
            <div>
              <dt>状态</dt>
              <dd className={`run-state run-state-${run.state}`}>
                {runStateLabel(run.state)}
              </dd>
            </div>
            <div>
              <dt>运行 ID</dt>
              <dd className="run-details-mono">{run.id}</dd>
            </div>
            {model !== null ? <DetailRow label="模型" value={model} /> : null}
          </Fragment>
        ) : null}
        {sessionTotals !== null ? (
          <Fragment>
            <DetailRow label="运行次数" value={String(sessionTotals.run_count)} />
            <DetailRow label="轮次" value={String(sessionTotals.round_count)} />
            <DetailRow label="重试" value={String(sessionTotals.retry_count)} />
            <div>
              <dt>累计 Token</dt>
              <dd>
                <TokenMetrics totals={sessionTotals} />
                <span className="run-details-note">
                  会话内全部运行累计的已知用量，非当前上下文占用。
                </span>
              </dd>
            </div>
          </Fragment>
        ) : null}
        {run !== null ? (
          <Fragment>
            {stopOutcome !== null ? (
              <DetailRow label="停止原因" value={stopOutcome.title} />
            ) : null}
            {run.error_kind !== null && errorOutcome !== null ? (
              <DetailRow label="错误类型" value={errorOutcome.title} />
            ) : null}
            <div>
              <dt>开始时间</dt>
              <dd>{new Date(run.started_at).toLocaleString()}</dd>
            </div>
            {run.finished_at !== null ? (
              <DetailRow
                label="结束时间"
                value={new Date(run.finished_at).toLocaleString()}
              />
            ) : null}
          </Fragment>
        ) : null}
      </dl>
      {contextLoad !== null ? <ContextLoadList load={contextLoad} /> : null}
      {stopOutcome !== null ? (
        <div className="run-stop-detail">
          <p>{stopOutcome.description}</p>
          {stopOutcome.hint !== null ? (
            <p className="run-stop-hint">{stopOutcome.hint}</p>
          ) : null}
          <p className="run-details-note">原始代码：{stopOutcome.code}</p>
        </div>
      ) : null}
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

/**
 * The token counters of the narrow rail: each metric is one unbreakable unit so the
 * line wraps between `输入` / `输出` / `缓存写入` / `缓存读取` instead of tearing a
 * label or its number apart (UX wave item 7).
 */
function TokenMetrics({ totals }: { totals: SessionTotalsDto }) {
  const metrics: Array<[string, number]> = [
    ["输入", totals.input_tokens],
    ["输出", totals.output_tokens],
    ["缓存写入", totals.cache_creation_input_tokens],
    ["缓存读取", totals.cache_read_input_tokens],
  ];
  return (
    <span className="run-token-metrics">
      {metrics.map(([label, value], index) => (
        <Fragment key={label}>
          {index > 0 ? " · " : null}
          <span className="run-token-metric">
            {label} {value.toLocaleString()}
          </span>
        </Fragment>
      ))}
    </span>
  );
}

/**
 * What the session loaded into its system context (spec 13.5): the AGENTS.md some
 * run of the session read, and every skill the model actually pulled through the
 * skill tool — never the discovered index. Both accumulate across runs, so the
 * section survives new requests; the server reports `null` only before the first
 * run, and the section simply stays hidden then.
 */
function ContextLoadList({ load }: { load: ContextLoadDto }) {
  return (
    <section className="run-context-load" aria-label="已加载上下文">
      <h3>已加载上下文</h3>
      <p className="run-context-agents">
        {load.agents_md_path !== null
          ? `AGENTS.md：${load.agents_md_path}`
          : "本会话未读取 AGENTS.md"}
      </p>
      {load.skills.length > 0 ? (
        <ul className="run-context-skills">
          {load.skills.map((skill) => (
            <li key={skill}>{skill}</li>
          ))}
        </ul>
      ) : (
        <p>本会话未读取技能。</p>
      )}
    </section>
  );
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
