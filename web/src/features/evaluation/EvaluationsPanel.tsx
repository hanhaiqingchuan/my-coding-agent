import { useEffect, useState } from "react";

import { EvaluationDetail } from "./EvaluationDetail";
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
  return rate === null ? "n/a" : `${(rate * 100).toFixed(1)}%`;
}

function timestamp(value: string | null): string {
  if (value === null) {
    return "unknown";
  }
  return value.replace(/\.\d+/, "");
}

function campaignName(summary: CampaignSummaryDto): string {
  return summary.campaign_id ?? summary.directory;
}

function judgeMeans(summary: CampaignSummaryDto): string {
  if (summary.judged_runs === 0) {
    return "not judged";
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
            <p>Evaluations</p>
            <h1>Evaluation campaigns</h1>
          </div>
        </header>
        <section className="eval-list" aria-label="Evaluation campaigns">
          {campaigns.status === "loading" ? (
            <p className="eval-muted">Loading evaluation campaigns…</p>
          ) : campaigns.status === "error" ? (
            <p className="eval-warning" role="alert">
              Unable to load evaluation campaigns.
            </p>
          ) : campaigns.data.length === 0 ? (
            <p className="eval-muted">
              No evaluation campaigns found — run `coding-agent-eval run` to
              create one
            </p>
          ) : (
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
                        <span className="eval-badge eval-fail">corrupt</span>
                      ) : null}
                    </span>
                    <small className="eval-campaign-window">
                      {timestamp(summary.started_at)} →{" "}
                      {timestamp(summary.finished_at)}
                    </small>
                    <small className="eval-campaign-metrics">
                      <span>{summary.task_count} tasks</span>
                      <span>{summary.started_runs} runs</span>
                      <span>
                        <b>{percent(summary.strict_success_rate)}</b> strict
                      </span>
                      {summary.judged_runs > 0 ? (
                        <span>
                          judge <b>{judgeMeans(summary)}</b>
                        </span>
                      ) : (
                        <span>not judged</span>
                      )}
                      <span>{summary.model_name ?? "unknown model"}</span>
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
            <p>Campaign</p>
            <h1>{screen.campaign}</h1>
          </div>
          <button
            type="button"
            className="eval-back"
            onClick={() => setScreen({ kind: "list" })}
          >
            Back to campaigns
          </button>
        </header>
        {detail.status === "loading" ? (
          <p className="eval-muted">Loading campaign…</p>
        ) : detail.status === "error" ? (
          <p className="eval-warning" role="alert">
            Unable to load this campaign.
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
          <p>Campaign</p>
          <h1>{screen.campaign}</h1>
        </div>
        <button
          type="button"
          className="eval-back"
          onClick={() =>
            setScreen({ kind: "campaign", campaign: screen.campaign })
          }
        >
          Back to campaign
        </button>
      </header>
      {run.status === "loading" ? (
        <p className="eval-muted">Loading run…</p>
      ) : run.status === "error" ? (
        <p className="eval-warning" role="alert">
          Unable to load this run.
        </p>
      ) : (
        <RunDetail detail={run.data} />
      )}
    </div>
  );
}
