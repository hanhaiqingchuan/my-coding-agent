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
      timeout_seconds: 1,
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

function commandTool(
  toolCallId: string,
  assistantMessageId: string,
  callOrder: number,
  marker: string,
): ToolExecutionDto {
  return {
    ...tool("succeeded"),
    tool_call_id: toolCallId,
    assistant_message_id: assistantMessageId,
    call_order: callOrder,
    input: {
      command: `echo ${marker}`,
      cwd: "/workspace",
      reason: "Check directory",
      timeout_seconds: 1,
    },
  };
}

function assistantMessageWith(id: string, marker: string): MessageDto {
  return {
    ...assistantMessage,
    id,
    seq: Number(id.split("-")[1] ?? 0),
    parts: [{ type: "text", text: marker }],
  };
}

/** Marker token of every rendered timeline entry, in DOM order. */
function timelineMarkers(): string[] {
  return screen
    .getAllByRole("article")
    .map(
      (entry) => /(round-\d+-[a-z]+)/.exec(entry.textContent ?? "")?.[1] ?? "",
    )
    .filter((marker) => marker.length > 0);
}

test("renders tool cards in the server order even when call_order restarts each round", () => {
  // call_order is the index inside one assistant message, so a later round starts at 0
  // again; only the backend's array order describes the real execution sequence.
  render(
    <ConversationTimeline
      messages={[]}
      tools={[
        commandTool("call-a", "message-1", 0, "round-1-first"),
        commandTool("call-b", "message-1", 1, "round-1-second"),
        commandTool("call-c", "message-2", 0, "round-2-first"),
        commandTool("call-d", "message-3", 0, "round-3-first"),
      ]}
      assistantDrafts={{}}
      toolOutputDrafts={{}}
    />,
  );

  expect(timelineMarkers()).toEqual([
    "round-1-first",
    "round-1-second",
    "round-2-first",
    "round-3-first",
  ]);
});

test("interleaves every tool card with the assistant message that requested it", () => {
  render(
    <ConversationTimeline
      messages={[
        assistantMessageWith("message-1", "round-1-answer"),
        assistantMessageWith("message-2", "round-2-answer"),
      ]}
      tools={[
        commandTool("call-a", "message-1", 0, "round-1-first"),
        commandTool("call-b", "message-1", 1, "round-1-second"),
        commandTool("call-c", "message-2", 0, "round-2-first"),
      ]}
      assistantDrafts={{}}
      toolOutputDrafts={{}}
    />,
  );

  expect(timelineMarkers()).toEqual([
    "round-1-answer",
    "round-1-first",
    "round-1-second",
    "round-2-answer",
    "round-2-first",
  ]);
});

test("keeps the streaming draft ahead of the tools its uncommitted message requested", () => {
  render(
    <ConversationTimeline
      messages={[assistantMessageWith("message-1", "round-1-answer")]}
      tools={[
        commandTool("call-a", "message-1", 0, "round-1-first"),
        commandTool("call-b", "message-2", 0, "round-2-first"),
      ]}
      assistantDrafts={{ "attempt-2": "round-2-draft" }}
      toolOutputDrafts={{}}
    />,
  );

  expect(timelineMarkers()).toEqual([
    "round-1-answer",
    "round-1-first",
    "round-2-draft",
    "round-2-first",
  ]);
});

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
