import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import type { JsonValue, RunDto, SessionSnapshotDto } from "../api/types";
import { RunDetailsPanel } from "./RunDetailsPanel";

afterEach(cleanup);

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

function snapshotWithRun(run: Partial<RunDto>): SessionSnapshotDto {
  return {
    session: {
      id: "session-1",
      title: null,
      workspace_realpath: "/workspace",
      requires_recovery_ack: false,
      created_at: "2026-08-28T00:00:00Z",
      updated_at: "2026-08-28T00:00:00Z",
    },
    active_run: {
      id: "run-1",
      session_id: "session-1",
      state: "retry_wait",
      stop_reason: null,
      error_kind: null,
      cancellation_requested_at: null,
      config_snapshot: NESTED_MODEL_CONFIG,
      started_at: "2026-08-28T00:00:00Z",
      finished_at: null,
      totals: {
        input_tokens: 0,
        output_tokens: 0,
        cache_creation_input_tokens: 0,
        cache_read_input_tokens: 0,
        round_count: 0,
        retry_count: 0,
      },
      ...run,
    },
    messages: [],
    tools: [],
    pending_approval: null,
    interrupted_banner: null,
    snapshot_seq: 1,
  };
}

function rowValue(label: string): string | undefined {
  return screen.getByText(label).parentElement?.querySelector("dd")
    ?.textContent;
}

test("shows the run state, model, rounds, retries and stop reason the backend published", () => {
  const snapshot = snapshotWithRun({
    state: "retry_wait",
    stop_reason: "retry_exhausted",
    error_kind: "retry_exhausted",
    totals: {
      input_tokens: 24,
      output_tokens: 12,
      cache_creation_input_tokens: 2,
      cache_read_input_tokens: 5,
      round_count: 3,
      retry_count: 2,
    },
  });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(rowValue("State")).toBe("retry_wait");
  expect(rowValue("Run ID")).toBe("run-1");
  expect(rowValue("Model")).toBe("demo-model");
  expect(rowValue("Rounds")).toBe("3");
  expect(rowValue("Retries")).toBe("2");
  expect(rowValue("Stop reason")).toBe("retry exhausted");
  expect(rowValue("Error kind")).toBe("retry exhausted");
});

test("labels the token totals as cumulative known usage across rounds", () => {
  const snapshot = snapshotWithRun({
    totals: {
      input_tokens: 24,
      output_tokens: 12,
      cache_creation_input_tokens: 2,
      cache_read_input_tokens: 5,
      round_count: 3,
      retry_count: 2,
    },
  });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(
    screen.getByText("input 24 · output 12 · cache create 2 · cache read 5"),
  ).not.toBeNull();
  expect(
    screen.getByText(/Known usage summed across rounds, not current context/),
  ).not.toBeNull();
});

test("never presents a context-occupancy figure the run row cannot support", () => {
  const snapshot = snapshotWithRun({
    totals: {
      input_tokens: 24,
      output_tokens: 12,
      cache_creation_input_tokens: 2,
      cache_read_input_tokens: 5,
      round_count: 3,
      retry_count: 2,
    },
  });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(screen.queryByText("Context")).toBeNull();
  expect(screen.queryByText(/%/)).toBeNull();
  expect(screen.queryByText(/4096/)).toBeNull();
});

test("omits run-detail rows when the backend did not provide their values", () => {
  const snapshot = snapshotWithRun({
    state: "model_streaming",
    stop_reason: null,
    error_kind: null,
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
