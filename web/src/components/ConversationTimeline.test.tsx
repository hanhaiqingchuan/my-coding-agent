import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import type {
  MessageDto,
  ToolExecutionDto,
  ToolExecutionState,
} from "../api/types";
import { ConversationTimeline } from "./ConversationTimeline";

afterEach(cleanup);

const assistantMessage: MessageDto = {
  id: "message-1",
  session_id: "session-1",
  run_id: "run-1",
  seq: 1,
  role: "assistant",
  parts: [{ type: "text", text: "Committed answer" }],
  status: "committed",
  tool_call_id: null,
};

function tool(state: ToolExecutionState): ToolExecutionDto {
  return {
    tool_call_id: `call-${state}`,
    run_id: "run-1",
    assistant_message_id: "message-1",
    call_order: 1,
    name: "command",
    input: {
      command: "pwd",
      cwd: "/workspace",
      reason: "Check directory",
      timeout_ms: 1000,
    },
    requires_approval: state === "awaiting_approval",
    approval_status: state === "awaiting_approval" ? "pending" : "approved",
    approval_decision: null,
    approval_decided_at: null,
    execution_state: state,
    result: null,
    duration_ms: null,
  };
}

test("renders committed messages separately from transient assistant drafts", () => {
  render(
    <ConversationTimeline
      messages={[assistantMessage]}
      tools={[]}
      assistantDrafts={{ "attempt-1": "Streaming answer" }}
      toolOutputDrafts={{}}
    />,
  );

  expect(screen.getByText("Committed answer")).not.toBeNull();
  expect(
    screen
      .getByText("Streaming answer")
      .closest("article")
      ?.getAttribute("data-transient"),
  ).toBe("true");
});

test.each<ToolExecutionState>([
  "queued",
  "awaiting_approval",
  "running",
  "succeeded",
  "failed",
  "rejected",
  "cancelled",
  "skipped",
  "unknown",
])("renders the %s tool execution status", (state) => {
  render(
    <ConversationTimeline
      messages={[]}
      tools={[tool(state)]}
      assistantDrafts={{}}
      toolOutputDrafts={{}}
    />,
  );

  expect(screen.getByText(state.replaceAll("_", " "))).not.toBeNull();
});

test("requires recovery acknowledgement before continuing after a server restart", async () => {
  const user = userEvent.setup();
  const onAcknowledgeRecovery = vi.fn();

  render(
    <ConversationTimeline
      messages={[]}
      tools={[]}
      assistantDrafts={{}}
      toolOutputDrafts={{}}
      interruptedBanner={{
        run_id: "run-1",
        stop_reason: "server_restart",
        requires_recovery_ack: true,
      }}
      onAcknowledgeRecovery={onAcknowledgeRecovery}
    />,
  );

  expect(screen.getByRole("alert").textContent).toContain(
    "上一 run 因服务重启而中断",
  );
  await user.click(
    screen.getByRole("button", { name: "我已检查 workspace/进程" }),
  );
  expect(onAcknowledgeRecovery).toHaveBeenCalledTimes(1);
});

test("shows a non-blocking historical interruption banner when acknowledgement is not required", () => {
  render(
    <ConversationTimeline
      messages={[]}
      tools={[]}
      assistantDrafts={{}}
      toolOutputDrafts={{}}
      interruptedBanner={{
        run_id: "run-1",
        stop_reason: "server_restart",
        requires_recovery_ack: false,
      }}
    />,
  );

  expect(screen.getByRole("alert").textContent).toContain("可以继续对话");
  expect(
    screen.queryByRole("button", { name: "我已检查 workspace/进程" }),
  ).toBeNull();
});
