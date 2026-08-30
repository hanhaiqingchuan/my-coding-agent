import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";

import type { ToolExecutionDto } from "../api/types";
import { ToolCard } from "./ToolCard";

afterEach(cleanup);

test("reveals complete command context in collapsed tool details without the dropped warning", async () => {
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
  await user.click(screen.getByText("详情"));

  const card = screen.getByRole("article", { name: "command 成功" });
  expect(card.textContent).toContain("git status --short");
  expect(card.textContent).toContain("/workspace");
  expect(card.textContent).toContain("Inspect changes");
  expect(card.textContent).toContain("120s");
  expect(card.textContent).not.toContain("沙箱");
});

test("reveals the real run_command timeout the backend froze", async () => {
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
  await user.click(screen.getByText("详情"));

  const card = screen.getByRole("article", { name: "run_command 成功" });
  expect(card.textContent).toContain("pytest -q");
  expect(card.textContent).toContain("30s");
  expect(card.textContent).not.toContain("沙箱");
});

function writeTool(input: ToolExecutionDto["input"]): ToolExecutionDto {
  return {
    tool_call_id: "call-2",
    run_id: "run-1",
    assistant_message_id: "message-1",
    call_order: 2,
    name: "write_file",
    input,
    requires_approval: true,
    approval_status: "approved",
    approval_decision: "approve",
    approval_decided_at: "2026-08-28T00:01:00Z",
    execution_state: "succeeded",
    result: null,
    duration_ms: 12,
  };
}

test("reveals the frozen replacement arguments of a write_file replace", async () => {
  const user = userEvent.setup();
  // write_file's schema is operation/path/content/old_text/new_text/replace_all with
  // additionalProperties false, so a replace call carries no content and no diff.
  const tool = writeTool({
    operation: "replace",
    path: "/workspace/src/app.py",
    old_text: "raise NotImplementedError()",
    new_text: "return compute(value)",
    replace_all: false,
  });

  render(<ToolCard tool={tool} />);
  await user.click(screen.getByText("详情"));

  const card = screen.getByRole("article", { name: "write_file 成功" });
  expect(card.textContent).toContain("replace");
  expect(card.textContent).toContain("/workspace/src/app.py");
  expect(card.textContent).toContain("raise NotImplementedError()");
  expect(card.textContent).toContain("return compute(value)");
  expect(card.textContent).toContain("false");
});

test("reveals the frozen new content of a write_file write without calling it a diff", async () => {
  const user = userEvent.setup();
  const tool = writeTool({
    operation: "write",
    path: "/workspace/notes.md",
    content: "# Notes\nwritten by the model\n",
  });

  render(<ToolCard tool={tool} />);
  await user.click(screen.getByText("详情"));

  const card = screen.getByRole("article", { name: "write_file 成功" });
  expect(card.textContent).toContain("write");
  expect(card.textContent).toContain("/workspace/notes.md");
  const contentBlock = card.querySelector("pre");
  expect(contentBlock?.textContent).toBe("# Notes\nwritten by the model\n");
  expect(card.querySelector(".tool-diff")).toBeNull();
});

test("truncates oversized write content and says how much it is showing", async () => {
  const user = userEvent.setup();
  const content = "x".repeat(5_000);
  const tool = writeTool({
    operation: "write",
    path: "/workspace/large.txt",
    content,
  });

  render(<ToolCard tool={tool} />);
  await user.click(screen.getByText("详情"));

  const card = screen.getByRole("article", { name: "write_file 成功" });
  expect(card.textContent).not.toContain(content);
  expect(card.textContent).toContain(
    "已截断：仅显示前 2000 字符（共 5000 字符）",
  );
});
