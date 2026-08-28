import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import type { PendingApprovalDto, ToolExecutionDto } from "../api/types";
import { ApprovalDock } from "./ApprovalDock";
import { Composer } from "./Composer";
import { ConversationTimeline } from "./ConversationTimeline";

afterEach(cleanup);

const pendingCommand: PendingApprovalDto = {
  run_id: "run-1",
  tool_call_id: "call-1",
  name: "command",
  input: {
    command: "git status --short",
    cwd: "/workspace",
    reason: "Inspect changes",
    timeout_ms: 120000,
  },
  target: null,
  preview: null,
  metadata: { sandboxed: false },
};

const resolvedTool: ToolExecutionDto = {
  tool_call_id: "call-1",
  run_id: "run-1",
  assistant_message_id: "message-1",
  call_order: 1,
  name: "command",
  input: pendingCommand.input,
  requires_approval: true,
  approval_status: "approved",
  approval_decision: "approve",
  approval_decided_at: "2026-08-28T00:01:00Z",
  execution_state: "succeeded",
  result: null,
  duration_ms: 12,
};

test("docks the current approval above the composer and resolves by call id and decision only", async () => {
  const user = userEvent.setup();
  const onResolve = vi.fn();

  render(
    <div>
      <ConversationTimeline
        messages={[]}
        tools={[]}
        assistantDrafts={{}}
        toolOutputDrafts={{}}
      />
      <ApprovalDock pendingApproval={pendingCommand} onResolve={onResolve} />
      <Composer
        activeRun={null}
        draft=""
        onDraftChange={vi.fn()}
        onSend={vi.fn()}
        onStop={vi.fn()}
      />
    </div>,
  );

  const dock = screen.getByRole("region", { name: "Pending approval" });
  const composer = screen.getByRole("textbox", { name: "Message" });
  expect(
    dock.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(dock.textContent).toContain("git status --short");
  expect(dock.textContent).toContain("/workspace");
  expect(dock.textContent).toContain("Inspect changes");
  expect(dock.textContent).toContain("120000");
  expect(dock.textContent).toContain("This command is not sandboxed");

  await user.click(screen.getByRole("button", { name: "Approve" }));
  expect(onResolve).toHaveBeenCalledTimes(1);
  expect(onResolve).toHaveBeenCalledWith("call-1", "approve");
});

test("removes a handled approval dock while its tool card remains in the timeline", () => {
  const { rerender } = render(
    <>
      <ConversationTimeline
        messages={[]}
        tools={[]}
        assistantDrafts={{}}
        toolOutputDrafts={{}}
      />
      <ApprovalDock pendingApproval={pendingCommand} onResolve={vi.fn()} />
    </>,
  );
  expect(
    screen.getByRole("region", { name: "Pending approval" }),
  ).not.toBeNull();

  rerender(
    <>
      <ConversationTimeline
        messages={[]}
        tools={[resolvedTool]}
        assistantDrafts={{}}
        toolOutputDrafts={{}}
      />
      <ApprovalDock pendingApproval={null} onResolve={vi.fn()} />
    </>,
  );

  expect(screen.queryByRole("region", { name: "Pending approval" })).toBeNull();
  expect(screen.getByText("command")).not.toBeNull();
});

test("shows a diff preview for write approvals", () => {
  render(
    <ApprovalDock
      pendingApproval={{
        ...pendingCommand,
        name: "write_file",
        preview: "@@ -1 +1 @@\n-old\n+new",
        metadata: {},
      }}
      onResolve={vi.fn()}
    />,
  );

  expect(screen.getByText(/@@ -1 \+1 @@/).textContent).toBe(
    "@@ -1 +1 @@\n-old\n+new",
  );
});
