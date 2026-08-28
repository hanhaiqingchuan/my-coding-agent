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
    timeout_seconds: 120,
  },
  target: null,
  preview: null,
  metadata: {
    command: "git status --short",
    cwd: "/workspace",
    relative_cwd: ".",
    reason: "Inspect changes",
    timeout_seconds: 120,
  },
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

const pendingRunCommand: PendingApprovalDto = {
  run_id: "run-1",
  tool_call_id: "call-2",
  name: "run_command",
  input: {
    command: "pytest -q",
    cwd: ".",
    reason: "Run the offline suite",
    timeout_seconds: 10,
  },
  target: "/workspace",
  preview: null,
  metadata: {
    command: "pytest -q",
    cwd: "/workspace",
    relative_cwd: ".",
    reason: "Run the offline suite",
    timeout_seconds: 10,
  },
};

test("shows the frozen run_command timeout in seconds and always warns that the command is not sandboxed", () => {
  render(
    <ApprovalDock pendingApproval={pendingRunCommand} onResolve={vi.fn()} />,
  );

  const dock = screen.getByRole("region", { name: "Pending approval" });
  expect(dock.textContent).toContain("pytest -q");
  expect(dock.textContent).toContain("Run the offline suite");
  expect(screen.getByText("10s")).not.toBeNull();
  expect(dock.textContent).toContain("This command is not sandboxed");
});

test("prefers the effective cwd and timeout the backend froze for a bare command call", () => {
  // The common call omits cwd and timeout_seconds; run_command freezes the effective
  // values into the approval metadata, which is the only place they exist.
  render(
    <ApprovalDock
      pendingApproval={{
        run_id: "run-1",
        tool_call_id: "call-3",
        name: "run_command",
        input: { command: "pytest -q" },
        target: "/workspace/services",
        preview: null,
        metadata: {
          command: "pytest -q",
          cwd: "/workspace/services",
          relative_cwd: "services",
          reason: null,
          timeout_seconds: 120,
        },
      }}
      onResolve={vi.fn()}
    />,
  );

  const dock = screen.getByRole("region", { name: "Pending approval" });
  expect(dock.textContent).toContain("pytest -q");
  expect(screen.getByText("/workspace/services")).not.toBeNull();
  expect(screen.getByText("120s")).not.toBeNull();
  expect(dock.textContent).toContain("This command is not sandboxed");
});

test("marks the timeout as absent instead of inventing the schema default", () => {
  render(
    <ApprovalDock
      pendingApproval={{
        ...pendingRunCommand,
        input: {
          command: "pytest -q",
          cwd: ".",
          reason: "Run the offline suite",
        },
        metadata: {
          command: "pytest -q",
          cwd: "/workspace",
          relative_cwd: ".",
          reason: "Run the offline suite",
        },
      }}
      onResolve={vi.fn()}
    />,
  );

  const dock = screen.getByRole("region", { name: "Pending approval" });
  expect(dock.textContent).not.toContain("120");
  expect(screen.getByText("—")).not.toBeNull();
  expect(dock.textContent).toContain("This command is not sandboxed");
});

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
  expect(dock.textContent).toContain("120s");
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
        input: {
          operation: "write",
          path: "notes.md",
          content: "new\n",
        },
        target: "/workspace/notes.md",
        preview: "@@ -1 +1 @@\n-old\n+new",
        metadata: {
          operation: "write",
          path: "/workspace/notes.md",
          content: "new\n",
        },
      }}
      onResolve={vi.fn()}
    />,
  );

  expect(screen.getByText(/@@ -1 \+1 @@/).textContent).toBe(
    "@@ -1 +1 @@\n-old\n+new",
  );
});
