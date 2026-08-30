import { useEffect, useState } from "react";

import { EvaluationDetail } from "./EvaluationDetail";
import { MetricsCharts } from "./MetricsCharts";
import { RunDetail } from "./RunDetail";
import {
  SCORE_NAMES,
  type CampaignDetailDto,
  type CampaignSummaryDto,
  type EvaluationReader,
  type RunDetailDto,
} from "./types";

type Screen =
  | { kind: "list" }
  | { kind: "campaign"; campaign: string }
  | { kind: "run"; campaign: string; taskId: string; repeat: number };

type Load<T> =
  { status: "loading" } | { status: "error" } | { status: "ready"; data: T };

type EvaluationsPanelProps = {
  reader: EvaluationReader;
  /** A campaign deep-linked through `#/evaluations/<campaign>`, if any. */
  initialCampaign?: string | null;
};

function percent(rate: number | null): string {
  return rate === null ? "—" : `${(rate * 100).toFixed(1)}%`;
}

function timestamp(value: string | null): string {
  if (value === null) {
    return "未知";
  }
  return value.replace(/\.\d+/, "");
}

function campaignName(summary: CampaignSummaryDto): string {
  return summary.campaign_id ?? summary.directory;
}

/** Visual busy feedback beside the loading line; decorative only. */
function SkeletonRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="eval-skeleton" aria-hidden="true">
      {Array.from({ length: rows }, (_, index) => (
        <span key={index} style={{ animationDelay: `${index * 90}ms` }} />
      ))}
    </div>
  );
}

function judgeMeans(summary: CampaignSummaryDto): string {
  if (summary.judged_runs === 0) {
    return "未评判";
  }
  return SCORE_NAMES.map((name) => {
    const value = summary.judge_means[name];
    return value === null || value === undefined ? "—" : value.toFixed(1);
  }).join(" · ");
}

/**
 * The read-only evaluation results workbench: campaigns list, one campaign's
 * task rows, one run with its judgement. Every screen is a pure view of disk
 * state; running campaigns stays on the CLI. A deep-linked campaign seeds the
 * initial screen; App remounts the panel (via key) when that link changes.
 */
export function EvaluationsPanel({
  reader,
  initialCampaign = null,
}: EvaluationsPanelProps) {
  const [screen, setScreen] = useState<Screen>(
    initialCampaign === null
      ? { kind: "list" }
      : { kind: "campaign", campaign: initialCampaign },
  );
  const [campaigns, setCampaigns] = useState<Load<CampaignSummaryDto[]>>({
    status: "loading",
  });
  const [detail, setDetail] = useState<Load<CampaignDetailDto>>({
    status: "loading",
  });
  const [run, setRun] = useState<Load<RunDetailDto>>({ status: "loading" });

  useEffect(() => {
    if (screen.kind !== "list") {
      return;
    }
    let cancelled = false;
    setCampaigns({ status: "loading" });
    reader
      .listCampaigns()
      .then((data) => {
        if (!cancelled) setCampaigns({ status: "ready", data });
      })
      .catch(() => {
        if (!cancelled) setCampaigns({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [reader, screen.kind]);

  useEffect(() => {
    if (screen.kind !== "campaign") {
      return;
    }
    let cancelled = false;
    setDetail({ status: "loading" });
    reader
      .campaignDetail(screen.campaign)
      .then((data) => {
        if (!cancelled) setDetail({ status: "ready", data });
      })
      .catch(() => {
        if (!cancelled) setDetail({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [reader, screen]);

  useEffect(() => {
    if (screen.kind !== "run") {
      return;
    }
    let cancelled = false;
    setRun({ status: "loading" });
    reader
      .runDetail(screen.campaign, screen.taskId, screen.repeat)
      .then((data) => {
        if (!cancelled) setRun({ status: "ready", data });
      })
      .catch(() => {
        if (!cancelled) setRun({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [reader, screen]);

  if (screen.kind === "list") {
    return (
      <div className="eval-panel">
        <header className="conversation-heading">
          <div>
            <p>评测记录</p>
            <h1>评测轮次</h1>
          </div>
        </header>
        <section className="eval-list" aria-label="评测轮次">
          {campaigns.status === "loading" ? (
            <>
              <p className="eval-muted">正在加载评测轮次…</p>
              <SkeletonRows />
            </>
          ) : campaigns.status === "error" ? (
            <p className="eval-warning" role="alert">
              无法加载评测轮次。
            </p>
          ) : campaigns.data.length === 0 ? (
            <p className="eval-muted">
              尚无评测记录——先运行 make eval-judge 生成
            </p>
          ) : (
            <>
              <MetricsCharts campaigns={campaigns.data} />
              <ul>
                {campaigns.data.map((summary) => (
                  <li key={summary.directory}>
                    <button
                      type="button"
                      className="eval-campaign-row"
                      onClick={() =>
                        setScreen({
                          kind: "campaign",
                          campaign: summary.directory,
                        })
                      }
                    >
                      <span className="eval-campaign-name">
                        {campaignName(summary)}
                        {summary.corrupt ? (
                          <span className="eval-badge eval-fail">已损坏</span>
                        ) : null}
                      </span>
                      <small className="eval-campaign-window">
                        {timestamp(summary.started_at)} →{" "}
                        {timestamp(summary.finished_at)}
                      </small>
                      <small className="eval-campaign-metrics">
                        <span>{summary.task_count} 个任务</span>
                        <span>{summary.started_runs} 次运行</span>
                        <span>
                          严格成功 <b>{percent(summary.strict_success_rate)}</b>
                        </span>
                        {summary.judged_runs > 0 ? (
                          <span>
                            裁判 <b>{judgeMeans(summary)}</b>
                          </span>
                        ) : (
                          <span>未评判</span>
                        )}
                        <span>{summary.model_name ?? "未知模型"}</span>
                      </small>
                      {summary.corrupt && summary.note !== null ? (
                        <small className="eval-campaign-note">
                          {summary.note}
                        </small>
                      ) : null}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </div>
    );
  }

  if (screen.kind === "campaign") {
    return (
      <div className="eval-panel">
        <header className="conversation-heading">
          <div>
            <p>评测轮次</p>
            <h1>{screen.campaign}</h1>
          </div>
          <button
            type="button"
            className="eval-back"
            onClick={() => setScreen({ kind: "list" })}
          >
            返回轮次列表
          </button>
        </header>
        {detail.status === "loading" ? (
          <>
            <p className="eval-muted">正在加载轮次…</p>
            <SkeletonRows rows={5} />
          </>
        ) : detail.status === "error" ? (
          <p className="eval-warning" role="alert">
            无法加载该轮次。
          </p>
        ) : (
          <EvaluationDetail
            detail={detail.data}
            onOpenRun={(taskId, repeat) =>
              setScreen({
                kind: "run",
                campaign: screen.campaign,
                taskId,
                repeat,
              })
            }
          />
        )}
      </div>
    );
  }

  return (
    <div className="eval-panel">
      <header className="conversation-heading">
        <div>
          <p>评测轮次</p>
          <h1>{screen.campaign}</h1>
        </div>
        <button
          type="button"
          className="eval-back"
          onClick={() =>
            setScreen({ kind: "campaign", campaign: screen.campaign })
          }
        >
          返回该轮次
        </button>
      </header>
      {run.status === "loading" ? (
        <>
          <p className="eval-muted">正在加载运行…</p>
          <SkeletonRows rows={4} />
        </>
      ) : run.status === "error" ? (
        <p className="eval-warning" role="alert">
          无法加载该运行。
        </p>
      ) : (
        <RunDetail detail={run.data} />
      )}
    </div>
  );
}
