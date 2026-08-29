import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import type { SessionSnapshotDto } from "./api/types";
import type { CampaignDetailDto } from "./features/evaluation/types";
import fixture from "./features/evaluation/fixtures/evaluation.fixture.json";

const mocks = vi.hoisted(() => ({
  send: vi.fn(),
  dispatch: vi.fn(),
  campaignDetail: vi.fn(),
}));

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

vi.mock("./features/evaluation/evaluationApi", () => ({
  EvaluationClient: class {
    listCampaigns = async () => [];
    campaignDetail = mocks.campaignDetail;
    runDetail = vi.fn();
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
    config_snapshot: { model: { model: "demo-model", context_window: 4096 } },
    started_at: "2026-08-28T00:00:00Z",
    finished_at: null,
    totals: {
      input_tokens: 0,
      output_tokens: 0,
      cache_creation_input_tokens: 0,
      cache_read_input_tokens: 0,
      round_count: 1,
      retry_count: 0,
    },
  },
  last_finished_run: null,
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
      timeout_seconds: 1,
    },
    target: null,
    preview: null,
    metadata: {
      command: "pwd",
      cwd: "/workspace",
      relative_cwd: ".",
      reason: "Check workspace",
      timeout_seconds: 1,
    },
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
      thinkingDrafts: {},
      toolOutputDrafts: {},
      connection: "connected",
    },
    dispatch: mocks.dispatch,
    send: mocks.send,
  }),
}));

import App from "./App";

// The fixture was recorded from a real offline campaign, so the deep-link test
// asserts the campaign screen against the exact payload shape the API serves.
const CAMPAIGN_DETAIL = fixture.campaignDetail as unknown as CampaignDetailDto;

afterEach(() => {
  cleanup();
  // The hash outlives one test inside jsdom's shared window; reset it so every
  // test starts from the default (sessions) route.
  window.location.hash = "";
  mocks.send.mockClear();
  mocks.dispatch.mockClear();
  mocks.campaignDetail.mockClear();
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

test("the left rail switches views by writing the location hash", async () => {
  const user = userEvent.setup();
  render(<App />);

  // The mocked snapshot holds an active run, so the composer shows Stop.
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy(),
  );
  expect(window.location.hash).toBe("");

  await user.click(screen.getByRole("button", { name: "Evaluations" }));
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Evaluations" })
        .getAttribute("aria-current"),
    ).toBe("page"),
  );
  expect(window.location.hash).toBe("#/evaluations");
  await waitFor(() =>
    expect(screen.getByText(/No evaluation campaigns found/)).toBeTruthy(),
  );
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();

  await user.click(screen.getByRole("button", { name: "Sessions" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy(),
  );
  expect(window.location.hash).toBe("");
  expect(
    screen
      .getByRole("button", { name: "Evaluations" })
      .getAttribute("aria-current"),
  ).toBeNull();
});

test("restores the evaluations view when the hash is set before mount", async () => {
  window.location.hash = "#/evaluations";
  render(<App />);

  await waitFor(() =>
    expect(screen.getByText(/No evaluation campaigns found/)).toBeTruthy(),
  );
  expect(
    screen
      .getByRole("button", { name: "Evaluations" })
      .getAttribute("aria-current"),
  ).toBe("page");
  expect(
    screen
      .getByRole("button", { name: "Sessions" })
      .getAttribute("aria-current"),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
});

test("follows hashchange events so refresh, back and pasted links restore the view", async () => {
  render(<App />);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy(),
  );

  window.location.hash = "#/evaluations";
  await waitFor(() =>
    expect(screen.getByText(/No evaluation campaigns found/)).toBeTruthy(),
  );
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();

  window.location.hash = "";
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy(),
  );
});

test("deep-links to one campaign through #/evaluations/<campaign>", async () => {
  mocks.campaignDetail.mockResolvedValueOnce(CAMPAIGN_DETAIL);
  window.location.hash = "#/evaluations/judged-campaign";
  render(<App />);

  await waitFor(() =>
    expect(
      screen.getByRole("heading", { name: "judged-campaign" }),
    ).toBeTruthy(),
  );
  await waitFor(() => expect(screen.getByText("demo-task")).toBeTruthy());
  expect(mocks.campaignDetail).toHaveBeenCalledWith("judged-campaign");
});
