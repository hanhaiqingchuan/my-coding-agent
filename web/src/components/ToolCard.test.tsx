import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";

import type { ToolExecutionDto } from "../api/types";
import { ToolCard } from "./ToolCard";

afterEach(cleanup);

test("reveals complete command context and its non-sandbox warning in collapsed tool details", async () => {
  const user = userEvent.setup();
  const tool: ToolExecutionDto = {
    tool_call_id: "call-1",
    run_id: "run-1",
    assistant_message_id: "message-1",
    call_order: 1,
    name: "command",
    input: {
      command: "git status --short",
      cwd: "/workspace",
      reason: "Inspect changes",
      timeout_seconds: 120,
    },
    requires_approval: true,
    approval_status: "approved",
    approval_decision: "approve",
    approval_decided_at: "2026-08-28T00:01:00Z",
    execution_state: "succeeded",
    result: null,
    duration_ms: 12,
  };

  render(<ToolCard tool={tool} />);
  await user.click(screen.getByText("Details"));

  const card = screen.getByRole("article", { name: "command succeeded" });
  expect(card.textContent).toContain("git status --short");
  expect(card.textContent).toContain("/workspace");
  expect(card.textContent).toContain("Inspect changes");
  expect(card.textContent).toContain("120s");
  expect(card.textContent).toContain("This command is not sandboxed");
});

test("reveals the real run_command timeout and its unconditional non-sandbox warning", async () => {
  const user = userEvent.setup();
  const tool: ToolExecutionDto = {
    tool_call_id: "call-3",
    run_id: "run-1",
    assistant_message_id: "message-1",
    call_order: 3,
    name: "run_command",
    input: {
      command: "pytest -q",
      cwd: ".",
      reason: "Run the offline suite",
      timeout_seconds: 30,
    },
    requires_approval: true,
    approval_status: "approved",
    approval_decision: "approve",
    approval_decided_at: "2026-08-28T00:01:00Z",
    execution_state: "succeeded",
    result: null,
    duration_ms: 12,
  };

  render(<ToolCard tool={tool} />);
  await user.click(screen.getByText("Details"));

  const card = screen.getByRole("article", { name: "run_command succeeded" });
  expect(card.textContent).toContain("pytest -q");
  expect(card.textContent).toContain("30s");
  expect(card.textContent).toContain("This command is not sandboxed");
});

test("reveals a write diff in collapsed tool details", async () => {
  const user = userEvent.setup();
  const tool: ToolExecutionDto = {
    tool_call_id: "call-2",
    run_id: "run-1",
    assistant_message_id: "message-1",
    call_order: 2,
    name: "write_file",
    input: { diff: "@@ -1 +1 @@\n-old\n+new" },
    requires_approval: false,
    approval_status: "approved",
    approval_decision: null,
    approval_decided_at: null,
    execution_state: "succeeded",
    result: null,
    duration_ms: 12,
  };

  render(<ToolCard tool={tool} />);
  await user.click(screen.getByText("Details"));

  expect(screen.getByText(/@@ -1 \+1 @@/).textContent).toBe(
    "@@ -1 +1 @@\n-old\n+new",
  );
});
