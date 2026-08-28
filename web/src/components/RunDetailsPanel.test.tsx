import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import type {
  JsonValue,
  RunDto,
  RunTotalsDto,
  SessionSnapshotDto,
} from "../api/types";
import stylesheet from "../styles.css?raw";
import { RunDetailsPanel } from "./RunDetailsPanel";

afterEach(cleanup);

// vitest applies no CSS in jsdom, so the pill treatments are asserted against the
// stylesheet source, read with its comments removed.
const CSS_RULES = stylesheet.replaceAll(/\/\*[\s\S]*?\*\//g, "");

// The backend stores `asdict(AppSettings)`, so `config_snapshot.model` is a nested
// mapping and never a bare model name.
const NESTED_MODEL_CONFIG: Record<string, JsonValue> = {
  model: {
    base_url: "https://api.anthropic.com",
    model: "demo-model",
    api_key_env: "ANTHROPIC_API_KEY",
    context_window: 4096,
    max_output_tokens: 1024,
    stream: true,
  },
  agent: { max_rounds: 30 },
};

const ZERO_TOTALS: RunTotalsDto = {
  input_tokens: 0,
  output_tokens: 0,
  cache_creation_input_tokens: 0,
  cache_read_input_tokens: 0,
  round_count: 0,
  retry_count: 0,
};

const USED_TOTALS: RunTotalsDto = {
  input_tokens: 24,
  output_tokens: 12,
  cache_creation_input_tokens: 2,
  cache_read_input_tokens: 5,
  round_count: 3,
  retry_count: 2,
};

/**
 * `active_run` is strictly non-terminal, so a live run carries neither a stop reason
 * nor an error kind nor a finish time.
 */
const LIVE_RUN: RunDto = {
  id: "run-1",
  session_id: "session-1",
  state: "model_streaming",
  stop_reason: null,
  error_kind: null,
  cancellation_requested_at: null,
  config_snapshot: NESTED_MODEL_CONFIG,
  started_at: "2026-08-28T00:00:00Z",
  finished_at: null,
  totals: ZERO_TOTALS,
};

/**
 * Every stop reason is written by the statement that makes a run terminal, so the
 * run published as `last_finished_run` always carries one plus a finish time.
 */
const FINISHED_RUN: RunDto = {
  ...LIVE_RUN,
  state: "failed",
  stop_reason: "retry_exhausted",
  error_kind: "retry_exhausted",
  finished_at: "2026-08-28T00:04:00Z",
};

function snapshotWith(runs: {
  active_run?: RunDto | null;
  last_finished_run?: RunDto | null;
}): SessionSnapshotDto {
  return {
    session: {
      id: "session-1",
      title: null,
      workspace_realpath: "/workspace",
      requires_recovery_ack: false,
      created_at: "2026-08-28T00:00:00Z",
      updated_at: "2026-08-28T00:00:00Z",
    },
    active_run: runs.active_run ?? null,
    last_finished_run: runs.last_finished_run ?? null,
    messages: [],
    tools: [],
    pending_approval: null,
    interrupted_banner: null,
    snapshot_seq: 1,
  };
}

function activeRunSnapshot(run: Partial<RunDto> = {}): SessionSnapshotDto {
  return snapshotWith({ active_run: { ...LIVE_RUN, ...run } });
}

function finishedRunSnapshot(run: Partial<RunDto> = {}): SessionSnapshotDto {
  return snapshotWith({ last_finished_run: { ...FINISHED_RUN, ...run } });
}

function rowValue(label: string): string | undefined {
  return rowCell(label)?.textContent ?? undefined;
}

function rowCell(label: string): HTMLElement | null {
  return (
    screen.getByText(label).parentElement?.querySelector<HTMLElement>("dd") ??
    null
  );
}

test("shows the run state, model, rounds, retries and stop reason the backend published", () => {
  const snapshot = finishedRunSnapshot({ totals: USED_TOTALS });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(rowValue("State")).toBe("failed");
  expect(rowValue("Run ID")).toBe("run-1");
  expect(rowValue("Model")).toBe("demo-model");
  expect(rowValue("Rounds")).toBe("3");
  expect(rowValue("Retries")).toBe("2");
  expect(rowValue("Stop reason")).toBe("retry exhausted");
  expect(rowValue("Error kind")).toBe("retry exhausted");
});

test("keeps the finished run and its stop reason visible once no run is active", () => {
  render(<RunDetailsPanel snapshot={finishedRunSnapshot()} />);

  expect(
    screen.getByText("Last finished run. No run is active."),
  ).not.toBeNull();
  expect(screen.queryByText("No active run.")).toBeNull();
  expect(rowValue("State")).toBe("failed");
  expect(rowValue("Stop reason")).toBe("retry exhausted");
  expect(rowValue("Finished")).toBe(
    new Date("2026-08-28T00:04:00Z").toLocaleString(),
  );
});

test("reports the internal error stop reason the backend can publish", () => {
  const snapshot = finishedRunSnapshot({
    state: "failed",
    stop_reason: "internal_error",
    error_kind: "internal_error",
  });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(rowValue("Stop reason")).toBe("internal error");
  expect(rowValue("Error kind")).toBe("internal error");
});

test("presents a live run as the active one instead of the finished run", () => {
  const snapshot = snapshotWith({
    active_run: { ...LIVE_RUN, id: "run-2" },
    last_finished_run: FINISHED_RUN,
  });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(screen.getByText("Active run.")).not.toBeNull();
  expect(screen.queryByText(/Last finished run/)).toBeNull();
  expect(rowValue("Run ID")).toBe("run-2");
  expect(rowValue("State")).toBe("model_streaming");
  expect(screen.queryByText("Stop reason")).toBeNull();
  expect(screen.queryByText("Finished")).toBeNull();
});

test("labels the token totals as cumulative known usage across rounds", () => {
  const snapshot = activeRunSnapshot({ totals: USED_TOTALS });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(
    screen.getByText("input 24 · output 12 · cache create 2 · cache read 5"),
  ).not.toBeNull();
  expect(
    screen.getByText(/Known usage summed across rounds, not current context/),
  ).not.toBeNull();
});

test("never presents a context-occupancy figure the run row cannot support", () => {
  const snapshot = activeRunSnapshot({ totals: USED_TOTALS });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(screen.queryByText("Context")).toBeNull();
  expect(screen.queryByText(/%/)).toBeNull();
  expect(screen.queryByText(/4096/)).toBeNull();
});

test("omits run-detail rows when the backend did not provide their values", () => {
  const snapshot = activeRunSnapshot({
    state: "model_streaming",
    config_snapshot: { model: { context_window: 4096 } },
  });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(screen.queryByText("Model")).toBeNull();
  expect(screen.queryByText("Context")).toBeNull();
  expect(screen.queryByText("Stop reason")).toBeNull();
  expect(screen.queryByText("Error kind")).toBeNull();
  // Rounds and retries are stored counters, so zero is the published fact.
  expect(rowValue("Rounds")).toBe("0");
  expect(rowValue("Retries")).toBe("0");
});

test("reports that there is no active run when the snapshot has none", () => {
  render(<RunDetailsPanel snapshot={null} />);

  expect(screen.getByText("No active run.")).not.toBeNull();
});

test("reports no run at all when the session never finished one", () => {
  render(<RunDetailsPanel snapshot={snapshotWith({})} />);

  expect(screen.getByText("No active run.")).not.toBeNull();
  expect(screen.queryByText("State")).toBeNull();
});

// Spec 5.2 pairs each terminal state with the stop reasons that can produce it.
const TERMINAL_RUNS = [
  ["completed", "completed"],
  ["stopped", "max_rounds"],
  ["cancelled", "user_stop"],
  ["failed", "retry_exhausted"],
  ["interrupted", "server_restart"],
] as const;

test.each(TERMINAL_RUNS)(
  "marks a %s run with its own state class",
  (state, stopReason) => {
    const snapshot = finishedRunSnapshot({ state, stop_reason: stopReason });

    render(<RunDetailsPanel snapshot={snapshot} />);

    const pill = rowCell("State");
    expect(pill?.textContent).toBe(state);
    expect(pill?.className).toBe(`run-state run-state-${state}`);
  },
);

test("gives every terminal run state its own pill treatment", () => {
  const treatments = TERMINAL_RUNS.map(([state]) => {
    const selector = `.run-state-${state}`;
    const declarations = declarationsFor(selector);
    expect(declarations, selector).not.toBeNull();
    // Without both of these the pill falls back to the shared neutral fill.
    expect(Object.keys(declarations ?? {}), selector).toEqual(
      expect.arrayContaining(["color", "background"]),
    );
    return JSON.stringify([
      declarations?.color,
      declarations?.background,
      declarations?.["border-color"],
      declarations?.["border-style"],
    ]);
  });

  expect(new Set(treatments).size).toBe(TERMINAL_RUNS.length);
});

function declarationsFor(selector: string): Record<string, string> | null {
  for (const rule of CSS_RULES.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = rule[1].split(",").map((item) => item.trim());
    if (!selectors.includes(selector)) continue;
    const declarations: Record<string, string> = {};
    for (const declaration of rule[2].split(";")) {
      const separator = declaration.indexOf(":");
      if (separator === -1) continue;
      const property = declaration.slice(0, separator).trim();
      declarations[property] = declaration.slice(separator + 1).trim();
    }
    return declarations;
  }
  return null;
}
