import { useState } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import type { RunDto, RunState } from "../api/types";
import "../styles.css";
import { Composer } from "./Composer";

afterEach(cleanup);

function run(state: RunState): RunDto {
  return {
    id: "run-1",
    session_id: "session-1",
    state,
    stop_reason: null,
    error_kind: null,
    cancellation_requested_at: null,
    config_snapshot: {},
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
  };
}

test("replaces Send with Stop while a run is active", async () => {
  const user = userEvent.setup();
  const onStop = vi.fn();

  render(
    <Composer
      activeRun={run("model_streaming")}
      draft="next"
      onDraftChange={vi.fn()}
      onSend={vi.fn()}
      onStop={onStop}
    />,
  );

  expect(screen.queryByRole("button", { name: "发送" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "停止" }));
  expect(onStop).toHaveBeenCalledWith("run-1");
});

test.each<RunState>([
  "starting",
  "building_context",
  "compacting",
  "model_streaming",
  "retry_wait",
  "awaiting_approval",
  "tool_running",
])("shows Stop for the active %s state", (state) => {
  render(
    <Composer
      activeRun={run(state)}
      draft="next"
      onDraftChange={vi.fn()}
      onSend={vi.fn()}
      onStop={vi.fn()}
    />,
  );

  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "停止" }).disabled,
  ).toBe(false);
});

test("shows a disabled stopping control while cancellation is in progress", () => {
  render(
    <Composer
      activeRun={run("cancelling")}
      draft="next"
      onDraftChange={vi.fn()}
      onSend={vi.fn()}
      onStop={vi.fn()}
    />,
  );

  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "正在停止" })
      .disabled,
  ).toBe(true);
});

test.each<RunState>([
  "completed",
  "stopped",
  "cancelled",
  "failed",
  "interrupted",
])("returns Send after the %s state", (state) => {
  render(
    <Composer
      activeRun={run(state)}
      draft="next"
      onDraftChange={vi.fn()}
      onSend={vi.fn()}
      onStop={vi.fn()}
    />,
  );

  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "发送" }).disabled,
  ).toBe(false);
});

test("keeps the next draft editable but cannot send it during a run", async () => {
  const user = userEvent.setup();
  const onSend = vi.fn();

  render(<DraftHarness activeRun={run("tool_running")} onSend={onSend} />);

  await user.type(screen.getByRole("textbox", { name: "消息" }), " message");
  expect(
    screen.getByRole<HTMLTextAreaElement>("textbox", { name: "消息" }).value,
  ).toBe("next message");
  expect(onSend).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "发送" })).toBeNull();
});

test("disables Send for empty input and a recovery acknowledgement gate", () => {
  const { rerender } = render(
    <Composer
      activeRun={null}
      draft="   "
      onDraftChange={vi.fn()}
      onSend={vi.fn()}
      onStop={vi.fn()}
    />,
  );
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "发送" }).disabled,
  ).toBe(true);

  rerender(
    <Composer
      activeRun={null}
      draft="ready"
      isRecoveryBlocked
      onDraftChange={vi.fn()}
      onSend={vi.fn()}
      onStop={vi.fn()}
    />,
  );
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "发送" }).disabled,
  ).toBe(true);
});

test("keeps the message input disabled and explained until recovery is acknowledged", async () => {
  const user = userEvent.setup();
  const onDraftChange = vi.fn();
  render(
    <Composer
      activeRun={null}
      draft="ready"
      isRecoveryBlocked
      onDraftChange={onDraftChange}
      onSend={vi.fn()}
      onStop={vi.fn()}
    />,
  );

  const textarea = screen.getByRole<HTMLTextAreaElement>("textbox", {
    name: "消息",
  });
  expect(textarea.disabled).toBe(true);
  expect(
    screen.getByText("输入框已禁用：请先确认已检查工作区/进程。"),
  ).not.toBeNull();

  await user.type(textarea, " more");
  expect(onDraftChange).not.toHaveBeenCalled();
});

test("keeps a visible focus indicator on the message input for keyboard users", async () => {
  const user = userEvent.setup();
  render(
    <Composer
      activeRun={null}
      draft=""
      onDraftChange={vi.fn()}
      onSend={vi.fn()}
      onStop={vi.fn()}
    />,
  );

  await user.tab();
  const textarea = screen.getByRole("textbox", { name: "消息" });
  const composer = textarea.closest("form");

  expect(document.activeElement).toBe(textarea);
  expect(composer?.className).toContain("composer-focused");
});

function DraftHarness({
  activeRun,
  onSend,
}: {
  activeRun: RunDto | null;
  onSend: (content: string) => void;
}) {
  const [draft, setDraft] = useState("next");
  return (
    <Composer
      activeRun={activeRun}
      draft={draft}
      onDraftChange={setDraft}
      onSend={onSend}
      onStop={vi.fn()}
    />
  );
}
