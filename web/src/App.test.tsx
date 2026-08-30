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
  draftText: null as string | null,
  compaction: null as
    | { phase: "running" }
    | { phase: "finished"; beforeTokens: number; afterTokens: number }
    | null,
  /** `true` keeps the fixture's active run; `false` renders an idle session. */
  hasActiveRun: true,
  /** `false` un-gates the composer so send-path tests can click 发送. */
  recoveryAck: true,
}));

vi.mock("./api/client", () => ({
  ApiClient: class {
    listSessions = async () => [
      {
        id: "session-1",
        title: "Demo",
        workspace_realpath: "/workspace",
        requires_recovery_ack: true,
        auto_approve: false,
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
    auto_approve: false,
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
    context: null,
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
  context_load: null,
  snapshot_seq: 1,
};

vi.mock("./features/sessions/useSession", () => ({
  useSession: () => ({
    state: {
      snapshot: {
        ...snapshot,
        active_run: mocks.hasActiveRun ? snapshot.active_run : null,
        session: {
          ...snapshot.session,
          requires_recovery_ack: mocks.recoveryAck,
        },
      },
      draftText: mocks.draftText ?? "next task",
      assistantDrafts: {},
      thinkingDrafts: {},
      toolOutputDrafts: {},
      connection: "connected",
      compaction: mocks.compaction,
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
  mocks.draftText = null;
  mocks.compaction = null;
  mocks.hasActiveRun = true;
  mocks.recoveryAck = true;
  if (snapshot.active_run !== null) snapshot.active_run.context = null;
});

test("renders the fixed approval dock and sends only backend commands for approval and recovery", async () => {
  const user = userEvent.setup();
  render(<App />);

  await waitFor(() =>
    expect(screen.getByRole("button", { name: "批准" })).not.toBeNull(),
  );
  expect(
    screen.getByRole("region", { name: "待审批" }),
  ).not.toBeNull();

  await user.click(screen.getByRole("button", { name: "批准" }));
  await user.click(
    screen.getByRole("button", { name: "我已检查工作区/进程" }),
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
    expect(screen.getByRole("button", { name: "停止" })).toBeTruthy(),
  );
  expect(window.location.hash).toBe("");

  await user.click(screen.getByRole("button", { name: "评测记录" }));
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "评测记录" })
        .getAttribute("aria-current"),
    ).toBe("page"),
  );
  expect(window.location.hash).toBe("#/evaluations");
  await waitFor(() =>
    expect(screen.getByText(/尚无评测记录/)).toBeTruthy(),
  );
  expect(screen.queryByRole("button", { name: "停止" })).toBeNull();

  await user.click(screen.getByRole("button", { name: "会话" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "停止" })).toBeTruthy(),
  );
  expect(window.location.hash).toBe("");
  expect(
    screen
      .getByRole("button", { name: "评测记录" })
      .getAttribute("aria-current"),
  ).toBeNull();
});

test("restores the evaluations view when the hash is set before mount", async () => {
  window.location.hash = "#/evaluations";
  render(<App />);

  await waitFor(() =>
    expect(screen.getByText(/尚无评测记录/)).toBeTruthy(),
  );
  expect(
    screen
      .getByRole("button", { name: "评测记录" })
      .getAttribute("aria-current"),
  ).toBe("page");
  expect(
    screen
      .getByRole("button", { name: "会话" })
      .getAttribute("aria-current"),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "停止" })).toBeNull();
});

test("follows hashchange events so refresh, back and pasted links restore the view", async () => {
  render(<App />);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "停止" })).toBeTruthy(),
  );

  window.location.hash = "#/evaluations";
  await waitFor(() =>
    expect(screen.getByText(/尚无评测记录/)).toBeTruthy(),
  );
  expect(screen.queryByRole("button", { name: "停止" })).toBeNull();

  window.location.hash = "";
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "停止" })).toBeTruthy(),
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

test("sends session.compact for the /compact slash command instead of a run", async () => {
  mocks.draftText = "/compact";
  // The composer only shows Send on an idle, recovery-acknowledged session; while
  // a run is active it holds Stop and the backend rejects concurrent compaction.
  mocks.hasActiveRun = false;
  mocks.recoveryAck = false;
  const user = userEvent.setup();
  render(<App />);

  await waitFor(() =>
    expect(screen.getByRole("button", { name: "发送" })).toBeTruthy(),
  );
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(mocks.send).toHaveBeenCalledTimes(1);
  expect(mocks.send).toHaveBeenCalledWith(
    expect.objectContaining({
      type: "session.compact",
      client_command_id: expect.any(String),
      session_id: "session-1",
      payload: {},
    }),
  );
  expect(mocks.dispatch).toHaveBeenCalledWith({
    type: "draft.changed",
    draftText: "",
  });
});

test("sends session.clear for the /clear slash command instead of a run", async () => {
  mocks.draftText = "/clear";
  mocks.hasActiveRun = false;
  mocks.recoveryAck = false;
  const user = userEvent.setup();
  render(<App />);

  await waitFor(() =>
    expect(screen.getByRole("button", { name: "发送" })).toBeTruthy(),
  );
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(mocks.send).toHaveBeenCalledTimes(1);
  expect(mocks.send).toHaveBeenCalledWith(
    expect.objectContaining({
      type: "session.clear",
      client_command_id: expect.any(String),
      session_id: "session-1",
      payload: {},
    }),
  );
});

test("a normal message still starts a run", async () => {
  mocks.draftText = "hello there";
  mocks.hasActiveRun = false;
  mocks.recoveryAck = false;
  const user = userEvent.setup();
  render(<App />);

  await waitFor(() =>
    expect(screen.getByRole("button", { name: "发送" })).toBeTruthy(),
  );
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(mocks.send).toHaveBeenCalledTimes(1);
  expect(mocks.send).toHaveBeenCalledWith(
    expect.objectContaining({
      type: "run.start",
      session_id: "session-1",
      payload: { content: "hello there" },
    }),
  );
});

test("the approval toggle sends the persisted mode change for the session", async () => {
  const user = userEvent.setup();
  render(<App />);

  await waitFor(() =>
    expect(screen.getByRole("button", { name: "自动批准" })).toBeTruthy(),
  );

  await user.click(screen.getByRole("button", { name: "自动批准" }));

  expect(mocks.send).toHaveBeenCalledWith(
    expect.objectContaining({
      type: "session.set_approval_mode",
      session_id: "session-1",
      payload: { auto_approve: true },
    }),
  );
});

test("the context gauge under the composer reports the focus run estimate", async () => {
  const activeRun = snapshot.active_run;
  if (activeRun !== null) {
    activeRun.context = {
    estimated_tokens: 12_000,
    available_tokens: 60_000,
      window_tokens: 64_000,
    };
  }
  render(<App />);

  await waitFor(() =>
    expect(screen.getByRole("status", { name: "上下文占用" })).toBeTruthy(),
  );
  expect(screen.getByRole("status", { name: "上下文占用" }).textContent).toContain(
    "20%",
  );
});

test("the compaction chip renders the finished compaction next to the gauge", async () => {
  mocks.compaction = {
    phase: "finished",
    beforeTokens: 61_440,
    afterTokens: 33_200,
  };
  render(<App />);

  await waitFor(() =>
    expect(screen.getByText("上下文已压缩：61,440 → 33,200 tokens")).toBeTruthy(),
  );
});
