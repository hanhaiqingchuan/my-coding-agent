import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import type { SessionSnapshotDto } from "../api/types";
import { RunDetailsPanel } from "./RunDetailsPanel";

afterEach(cleanup);

test("shows model, round, context, retry, and stop reason from the backend run snapshot", () => {
  const snapshot: SessionSnapshotDto = {
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
      stop_reason: "retry_exhausted",
      error_kind: "retry_exhausted",
      cancellation_requested_at: null,
      config_snapshot: {
        model: "demo-model",
        round: 3,
        context_used: 1200,
        context_window: 4096,
        retry_attempt: 2,
      },
      started_at: "2026-08-28T00:00:00Z",
      finished_at: null,
    },
    messages: [],
    tools: [],
    pending_approval: null,
    interrupted_banner: null,
    snapshot_seq: 1,
  };

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(screen.getByText("demo-model")).not.toBeNull();
  expect(screen.getByText("3")).not.toBeNull();
  expect(screen.getByText("1200 / 4096")).not.toBeNull();
  expect(screen.getByText("2")).not.toBeNull();
  expect(screen.getByText("retry exhausted")).not.toBeNull();
});

test("omits run-detail rows when the backend did not provide their values", () => {
  const snapshot: SessionSnapshotDto = {
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
      state: "model_streaming",
      stop_reason: null,
      error_kind: null,
      cancellation_requested_at: null,
      config_snapshot: {},
      started_at: "2026-08-28T00:00:00Z",
      finished_at: null,
    },
    messages: [],
    tools: [],
    pending_approval: null,
    interrupted_banner: null,
    snapshot_seq: 1,
  };

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(screen.queryByText("Model")).toBeNull();
  expect(screen.queryByText("Round")).toBeNull();
  expect(screen.queryByText("Context")).toBeNull();
  expect(screen.queryByText("Retry")).toBeNull();
  expect(screen.queryByText("Stop reason")).toBeNull();
});
