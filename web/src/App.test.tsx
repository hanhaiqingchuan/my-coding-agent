import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import type { SessionSnapshotDto } from "./api/types";

const mocks = vi.hoisted(() => ({ send: vi.fn(), dispatch: vi.fn() }));

vi.mock("./api/client", () => ({
  ApiClient: class {
    listSessions = async () => [
      {
        id: "session-1",
        title: "Demo",
        workspace_realpath: "/workspace",
        requires_recovery_ack: true,
        created_at: "2026-08-28T00:00:00Z",
        updated_at: "2026-08-28T00:00:00Z",
      },
    ];
    createSession = vi.fn();
  },
}));

const snapshot: SessionSnapshotDto = {
  session: {
    id: "session-1",
    title: "Demo",
    workspace_realpath: "/workspace",
    requires_recovery_ack: true,
    created_at: "2026-08-28T00:00:00Z",
    updated_at: "2026-08-28T00:00:00Z",
  },
  active_run: {
    id: "run-1",
    session_id: "session-1",
    state: "awaiting_approval",
    stop_reason: null,
    error_kind: null,
    cancellation_requested_at: null,
    config_snapshot: { model: "demo-model" },
    started_at: "2026-08-28T00:00:00Z",
    finished_at: null,
  },
  messages: [],
  tools: [],
  pending_approval: {
    run_id: "run-1",
    tool_call_id: "call-1",
    name: "command",
    input: {
      command: "pwd",
      cwd: "/workspace",
      reason: "Check workspace",
      timeout_ms: 1000,
    },
    target: null,
    preview: null,
    metadata: {},
  },
  interrupted_banner: {
    run_id: "run-previous",
    stop_reason: "server_restart",
    requires_recovery_ack: true,
  },
  snapshot_seq: 1,
};

vi.mock("./features/sessions/useSession", () => ({
  useSession: () => ({
    state: {
      snapshot,
      draftText: "next task",
      assistantDrafts: {},
      toolOutputDrafts: {},
      connection: "connected",
    },
    dispatch: mocks.dispatch,
    send: mocks.send,
  }),
}));

import App from "./App";

afterEach(() => {
  cleanup();
  mocks.send.mockClear();
  mocks.dispatch.mockClear();
});

test("renders the fixed approval dock and sends only backend commands for approval and recovery", async () => {
  const user = userEvent.setup();
  render(<App />);

  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Approve" })).not.toBeNull(),
  );
  expect(
    screen.getByRole("region", { name: "Pending approval" }),
  ).not.toBeNull();

  await user.click(screen.getByRole("button", { name: "Approve" }));
  await user.click(
    screen.getByRole("button", { name: "我已检查 workspace/进程" }),
  );

  expect(mocks.send).toHaveBeenCalledWith(
    expect.objectContaining({
      type: "approval.resolve",
      session_id: "session-1",
      payload: { run_id: "run-1", tool_call_id: "call-1", decision: "approve" },
    }),
  );
  expect(mocks.send).toHaveBeenCalledWith(
    expect.objectContaining({
      type: "session.ack_recovery",
      session_id: "session-1",
      payload: {},
    }),
  );
});
