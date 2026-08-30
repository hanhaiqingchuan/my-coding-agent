import type { RunContextDto } from "../api/types";

type ContextGaugeProps = { context: RunContextDto | null };

/**
 * The composer's context gauge (spec: UX wave item 1). The percentage is strictly
 * evidence-based — the builder's latest estimate over the window the backend
 * reported — so a run without a recorded estimate renders nothing rather than an
 * invented figure. Levels mirror the compactor's thresholds: the bar turns amber
 * past 60% and red past 80%, where auto-compaction triggers.
 */
export function ContextGauge({ context }: ContextGaugeProps) {
  if (context === null || context.available_tokens <= 0) return null;
  const percent = Math.min(
    100,
    Math.round((context.estimated_tokens / context.available_tokens) * 100),
  );
  const level = percent >= 80 ? "high" : percent >= 60 ? "warn" : "ok";
  return (
    <p className="context-gauge" role="status" aria-label="上下文占用">
      <span className="context-gauge-label">上下文</span>
      <span className="context-gauge-bar" aria-hidden="true">
        <span
          className={`context-gauge-fill context-gauge-${level}`}
          style={{ width: `${percent}%` }}
        />
      </span>
      <span className="context-gauge-value">{percent}%</span>
      <span className="context-gauge-detail">
        {context.estimated_tokens.toLocaleString()} /{" "}
        {context.available_tokens.toLocaleString()} tokens
      </span>
    </p>
  );
}
