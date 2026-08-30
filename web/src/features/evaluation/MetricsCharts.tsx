import { SCORE_NAMES, type CampaignSummaryDto } from "./types";

/**
 * Per-metric campaign comparison: one small bar chart per metric, one bar per
 * campaign (chronological, matching the list below). Hand-rolled SVG keeps the
 * bundle free of a chart dependency and the style on the instrument language.
 */

type Metric = {
  key: string;
  label: string;
  hint?: string;
  extract(summary: CampaignSummaryDto): number | null;
  format(value: number): string;
};

const judgeMean = (summary: CampaignSummaryDto): number | null => {
  const values = SCORE_NAMES.map((name) => summary.judge_means[name]).filter(
    (value): value is number => typeof value === "number",
  );
  return values.length > 0
    ? values.reduce((total, value) => total + value, 0) / values.length
    : null;
};

const METRICS: Metric[] = [
  {
    key: "success",
    label: "任务成功率",
    extract: (s) =>
      s.strict_success_rate === null ? null : s.strict_success_rate * 100,
    format: (v) => `${v.toFixed(0)}%`,
  },
  {
    key: "rounds",
    label: "平均轮次",
    hint: "模型调用",
    extract: (s) => s.avg_rounds,
    format: (v) => v.toFixed(1),
  },
  {
    key: "tools",
    label: "平均工具执行",
    extract: (s) => s.avg_tool_calls,
    format: (v) => v.toFixed(1),
  },
  {
    key: "tool-failures",
    label: "平均工具失败",
    extract: (s) => s.avg_tool_failures,
    format: (v) => v.toFixed(1),
  },
  {
    key: "tokens",
    label: "平均 Token",
    hint: "输入 + 输出",
    extract: (s) =>
      s.avg_input_tokens === null && s.avg_output_tokens === null
        ? null
        : (s.avg_input_tokens ?? 0) + (s.avg_output_tokens ?? 0),
    format: (v) => Math.round(v).toLocaleString("en-US"),
  },
  {
    key: "duration",
    label: "平均耗时",
    hint: "秒",
    extract: (s) =>
      s.avg_duration_ms === null ? null : s.avg_duration_ms / 1000,
    format: (v) => `${v.toFixed(1)}s`,
  },
  {
    key: "judge",
    label: "裁判均分",
    hint: "三项均值，1–5",
    extract: judgeMean,
    format: (v) => v.toFixed(2),
  },
];

/** campaign-20260830-184647 → 08-30 18:46; anything else passes through. */
function shortLabel(directory: string): string {
  const match = /^campaign-(?:\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(
    directory,
  );
  return match === null
    ? directory
    : `${match[1]}-${match[2]} ${match[3]}:${match[4]}`;
}

const CHART_W = 320;
const CHART_H = 128;
const BASE_Y = 100;
const MAX_BAR_H = 76;

function MetricChart({
  metric,
  campaigns,
}: {
  metric: Metric;
  campaigns: CampaignSummaryDto[];
}) {
  const values = campaigns.map((campaign) => metric.extract(campaign));
  const present = values.filter((value): value is number => value != null);
  const peak = present.length > 0 ? Math.max(...present) : 0;
  const slot = CHART_W / campaigns.length;
  const barWidth = Math.min(44, slot * 0.56);
  const heightOf = (value: number) =>
    peak === 0 ? 0 : Math.max(2, (value / peak) * MAX_BAR_H);
  const trend = campaigns
    .map((campaign, index) => {
      const value = values[index];
      if (value == null) return null;
      return { x: slot * index + slot / 2, y: BASE_Y - heightOf(value) };
    })
    .filter((point): point is { x: number; y: number } => point !== null);

  return (
    <figure className="metric-chart">
      <figcaption>
        {metric.label}
        {metric.hint !== undefined ? <small>{metric.hint}</small> : null}
      </figcaption>
      {present.length === 0 ? (
        <p className="eval-muted">暂无数据</p>
      ) : (
        <svg
          viewBox={`0 0 ${CHART_W} ${CHART_H}`}
          role="img"
          aria-label={`${metric.label}：各轮次对比`}
        >
          <line
            className="metric-baseline"
            x1={0}
            y1={BASE_Y}
            x2={CHART_W}
            y2={BASE_Y}
          />
          {campaigns.map((campaign, index) => {
            const value = values[index];
            const cx = slot * index + slot / 2;
            if (value == null) {
              return (
                <text
                  key={campaign.directory}
                  className="metric-null"
                  x={cx}
                  y={BASE_Y - 8}
                >
                  —
                </text>
              );
            }
            const height = heightOf(value);
            return (
              <g key={campaign.directory}>
                <title>{`${shortLabel(campaign.directory)}：${metric.format(value)}`}</title>
                <rect
                  className="metric-bar"
                  x={cx - barWidth / 2}
                  y={BASE_Y - height}
                  width={barWidth}
                  height={height}
                  rx={3}
                  style={{ animationDelay: `${index * 70}ms` }}
                />
                <text className="metric-value" x={cx} y={BASE_Y - height - 5}>
                  {metric.format(value)}
                </text>
                <text className="metric-label" x={cx} y={BASE_Y + 14}>
                  {shortLabel(campaign.directory)}
                </text>
              </g>
            );
          })}
          {trend.length > 1 ? (
            <polyline
              className="metric-trend"
              points={trend.map((point) => `${point.x},${point.y}`).join(" ")}
            />
          ) : null}
          {trend.map((point, index) => (
            <circle
              key={index}
              className="metric-trend-dot"
              cx={point.x}
              cy={point.y}
              r={3}
            />
          ))}
        </svg>
      )}
    </figure>
  );
}

export function MetricsCharts({
  campaigns,
}: {
  campaigns: CampaignSummaryDto[];
}) {
  const usable = campaigns.filter((campaign) => !campaign.corrupt);
  if (usable.length === 0) return null;
  return (
    <section className="eval-charts" aria-label="各轮次指标对比">
      <h2 className="eval-charts-title">各轮次指标对比</h2>
      <div className="eval-charts-grid">
        {METRICS.map((metric) => (
          <MetricChart key={metric.key} metric={metric} campaigns={usable} />
        ))}
      </div>
    </section>
  );
}
