import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { toolStateLabel } from "../api/labels";
import type {
  MessageDto,
  RunDto,
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
      thinkingDrafts={{}}
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
      thinkingDrafts={{}}
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
      thinkingDrafts={{}}
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
      thinkingDrafts={{}}
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

const thinkingMessage: MessageDto = {
  ...assistantMessage,
  id: "message-2",
  parts: [
    { type: "thinking", text: "Reasoning about the workspace change." },
    { type: "text", text: "Committed answer" },
  ],
};

test("renders a committed thinking part collapsed by default and expands on demand", async () => {
  const user = userEvent.setup();
  render(
    <ConversationTimeline
      messages={[thinkingMessage]}
      tools={[]}
      assistantDrafts={{}}
      thinkingDrafts={{}}
      toolOutputDrafts={{}}
    />,
  );

  const disclosure = screen.getByRole("button", { name: /^思考完成 · / });
  expect(disclosure.getAttribute("aria-expanded")).toBe("false");
  // The summary row reports the size of the reasoning it guards.
  expect(disclosure.textContent).toBe("思考完成 · 37 字");

  await user.click(disclosure);
  expect(disclosure.getAttribute("aria-expanded")).toBe("true");
  expect(
    screen.getByText("Reasoning about the workspace change."),
  ).not.toBeNull();

  // Part order is authoritative: the thinking disclosure precedes the answer text.
  const article = screen.getByText("Committed answer").closest("article");
  expect((article?.textContent ?? "").indexOf("思考完成")).toBeLessThan(
    (article?.textContent ?? "").indexOf("Committed answer"),
  );
});

test("renders a live thinking draft expanded while streaming and collapsed once closed", async () => {
  const user = userEvent.setup();
  const { rerender } = render(
    <ConversationTimeline
      messages={[]}
      tools={[]}
      assistantDrafts={{}}
      thinkingDrafts={{
        "attempt-1": { text: "Live reasoning.", closed: false },
      }}
      toolOutputDrafts={{}}
    />,
  );

  const disclosure = screen.getByRole("button", { name: /^思考中 · / });
  expect(disclosure.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("Live reasoning.")).not.toBeNull();
  expect(
    screen
      .getByText("Live reasoning.")
      .closest("article")
      ?.getAttribute("data-transient"),
  ).toBe("true");

  // The closed event only flips the reducer flag; the same disclosure collapses.
  rerender(
    <ConversationTimeline
      messages={[]}
      tools={[]}
      assistantDrafts={{}}
      thinkingDrafts={{
        "attempt-1": { text: "Live reasoning.", closed: true },
      }}
      toolOutputDrafts={{}}
    />,
  );

  expect(disclosure.getAttribute("aria-expanded")).toBe("false");
  expect(disclosure.closest(".thinking-box")?.getAttribute("data-open")).toBe(
    "false",
  );

  // A collapsed live box can still be reopened by hand.
  await user.click(disclosure);
  expect(disclosure.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("Live reasoning.")).not.toBeNull();
});

test("keeps the streaming text draft below the live thinking box of the same epoch", () => {
  render(
    <ConversationTimeline
      messages={[]}
      tools={[]}
      assistantDrafts={{ "attempt-1": "Streaming answer" }}
      thinkingDrafts={{
        "attempt-1": { text: "Live reasoning.", closed: false },
      }}
      toolOutputDrafts={{}}
    />,
  );

  const article = screen
    .getByText("Streaming answer")
    .closest("article[data-transient='true']");
  expect(article?.textContent).toContain("Live reasoning.");
  expect((article?.textContent ?? "").indexOf("思考中")).toBeLessThan(
    (article?.textContent ?? "").indexOf("Streaming answer"),
  );
  // One bubble per epoch: the thinking box never replaces the text draft.
  expect(screen.getAllByRole("article")).toHaveLength(1);
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
      thinkingDrafts={{}}
      toolOutputDrafts={{}}
    />,
  );

  expect(screen.getByText(toolStateLabel(state))).not.toBeNull();
});

test("requires recovery acknowledgement before continuing after a server restart", async () => {
  const user = userEvent.setup();
  const onAcknowledgeRecovery = vi.fn();

  render(
    <ConversationTimeline
      messages={[]}
      tools={[]}
      assistantDrafts={{}}
      thinkingDrafts={{}}
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
    "上一轮运行因服务重启而中断",
  );
  await user.click(screen.getByRole("button", { name: "我已检查工作区/进程" }));
  expect(onAcknowledgeRecovery).toHaveBeenCalledTimes(1);
});

test("shows a non-blocking historical interruption banner when acknowledgement is not required", () => {
  render(
    <ConversationTimeline
      messages={[]}
      tools={[]}
      assistantDrafts={{}}
      thinkingDrafts={{}}
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
    screen.queryByRole("button", { name: "我已检查工作区/进程" }),
  ).toBeNull();
});

const failedRun: RunDto = {
  id: "run-failed",
  session_id: "session-1",
  state: "failed",
  stop_reason: "retry_exhausted",
  error_kind: "retry_exhausted",
  cancellation_requested_at: null,
  config_snapshot: {},
  started_at: "2026-08-28T00:00:00Z",
  finished_at: "2026-08-28T00:04:00Z",
  totals: {
    input_tokens: 10,
    output_tokens: 5,
    cache_creation_input_tokens: 0,
    cache_read_input_tokens: 0,
    round_count: 1,
    retry_count: 3,
  },
  context: null,
};

test("summarizes a failed run as a dismissible banner with the retry fact and hint", async () => {
  const user = userEvent.setup();
  render(
    <ConversationTimeline
      messages={[]}
      tools={[]}
      assistantDrafts={{}}
      thinkingDrafts={{}}
      toolOutputDrafts={{}}
      lastFinishedRun={failedRun}
    />,
  );

  expect(screen.getByText("请求重试用尽")).not.toBeNull();
  expect(screen.getByText(/限流 429、服务端 5xx 或网络波动/)).not.toBeNull();
  expect(screen.getByText("已自动重试 3 次。")).not.toBeNull();
  expect(screen.getByText(/稍等片刻后重新发送消息即可重试/)).not.toBeNull();

  await user.click(screen.getByRole("button", { name: "关闭提示" }));
  expect(screen.queryByText("请求重试用尽")).toBeNull();
});

test("renders no failure banner when the last finished run was a user stop", () => {
  render(
    <ConversationTimeline
      messages={[]}
      tools={[]}
      assistantDrafts={{}}
      thinkingDrafts={{}}
      toolOutputDrafts={{}}
      lastFinishedRun={{
        ...failedRun,
        id: "run-stopped",
        state: "cancelled",
        stop_reason: "user_stop",
        error_kind: null,
      }}
    />,
  );

  expect(screen.queryByTestId("run-failure-banner")).toBeNull();
});

test("renders no failure banner while a run is still active", () => {
  render(
    <ConversationTimeline
      messages={[]}
      tools={[]}
      assistantDrafts={{}}
      thinkingDrafts={{}}
      toolOutputDrafts={{}}
      lastFinishedRun={null}
    />,
  );

  expect(screen.queryByTestId("run-failure-banner")).toBeNull();
});

/** Give the jsdom scroller real geometry so scroll pinning is observable. */
function withGeometry(element: HTMLElement, height: number): void {
  Object.defineProperty(element, "scrollHeight", {
    configurable: true,
    get: () => height,
  });
  Object.defineProperty(element, "clientHeight", {
    configurable: true,
    get: () => 300,
  });
}

test("keeps the newest content in view by scrolling to the bottom", () => {
  const { rerender } = render(
    <ConversationTimeline
      messages={[assistantMessageWith("message-1", "first")]}
      tools={[]}
      assistantDrafts={{}}
      thinkingDrafts={{}}
      toolOutputDrafts={{}}
    />,
  );
  const scroller = document.querySelector<HTMLElement>(".timeline-scroll");
  expect(scroller).not.toBeNull();
  withGeometry(scroller as HTMLElement, 1_000);

  rerender(
    <ConversationTimeline
      messages={[
        assistantMessageWith("message-1", "first"),
        assistantMessageWith("message-2", "second"),
      ]}
      tools={[]}
      assistantDrafts={{}}
      thinkingDrafts={{}}
      toolOutputDrafts={{}}
    />,
  );
  expect((scroller as HTMLElement).scrollTop).toBe(1_000);
});

test("a reader who scrolled up keeps their position", () => {
  const { rerender } = render(
    <ConversationTimeline
      messages={[assistantMessageWith("message-1", "first")]}
      tools={[]}
      assistantDrafts={{}}
      thinkingDrafts={{}}
      toolOutputDrafts={{}}
    />,
  );
  const scroller = document.querySelector<HTMLElement>(".timeline-scroll");
  withGeometry(scroller as HTMLElement, 1_000);
  // Reading an old message: 1000 - 0 - 300 = 700px above the bottom.
  (scroller as HTMLElement).scrollTop = 0;
  (scroller as HTMLElement).dispatchEvent(new Event("scroll"));

  rerender(
    <ConversationTimeline
      messages={[
        assistantMessageWith("message-1", "first"),
        assistantMessageWith("message-2", "second"),
      ]}
      tools={[]}
      assistantDrafts={{}}
      thinkingDrafts={{}}
      toolOutputDrafts={{}}
    />,
  );
  expect((scroller as HTMLElement).scrollTop).toBe(0);

  // Returning to the bottom re-arms the auto-scroll.
  (scroller as HTMLElement).scrollTop = 700;
  (scroller as HTMLElement).dispatchEvent(new Event("scroll"));
  rerender(
    <ConversationTimeline
      messages={[
        assistantMessageWith("message-1", "first"),
        assistantMessageWith("message-2", "second"),
        assistantMessageWith("message-3", "third"),
      ]}
      tools={[]}
      assistantDrafts={{}}
      thinkingDrafts={{}}
      toolOutputDrafts={{}}
    />,
  );
  expect((scroller as HTMLElement).scrollTop).toBe(1_000);
});
