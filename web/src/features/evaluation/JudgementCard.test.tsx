import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";

import type { JudgementDto } from "./types";
import { JudgementCard } from "./JudgementCard";

afterEach(cleanup);

const RATIONALE =
  "The goal was met with tidy tool use: the helper was created and the suite verified the change.";

const SCORED: JudgementDto = {
  schema_version: "judgement-v1",
  campaign_id: "campaign-judged",
  task_id: "demo-task",
  repeat: 1,
  judge_model: "claude-model-name",
  prompt_version: "judge-v1",
  scores: { task_completion: 4, process_quality: 5, communication: 3 },
  rationale: RATIONALE,
  error: null,
  error_detail: null,
};

test("shows the three scores with the rationale collapsed by default", async () => {
  const user = userEvent.setup();
  render(<JudgementCard judgement={SCORED} />);

  expect(screen.getByText("任务完成")).toBeTruthy();
  expect(screen.getByText("过程质量")).toBeTruthy();
  expect(screen.getByText("沟通表达")).toBeTruthy();
  expect(screen.getByText("4")).toBeTruthy();
  expect(screen.getByText("5")).toBeTruthy();
  expect(screen.getByText("3")).toBeTruthy();
  expect(screen.getByText(/claude-model-name/)).toBeTruthy();
  expect(screen.getByText("judge-v1")).toBeTruthy();

  const toggle = screen.getByRole("button", {
    name: /^裁判理由 · \d+ 字$/,
  });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");

  await user.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText(RATIONALE)).toBeTruthy();

  await user.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
});

test("reports a judge error without scores", () => {
  render(
    <JudgementCard
      judgement={{
        ...SCORED,
        scores: {},
        rationale: "",
        error: "judge_error",
        error_detail: "the judge request failed: ModelTransportError",
      }}
    />,
  );

  expect(screen.getByText("裁判错误")).toBeTruthy();
  expect(
    screen.getByText("the judge request failed: ModelTransportError"),
  ).toBeTruthy();
  expect(screen.queryByText("任务完成")).toBeNull();
});

test("renders a missing judgement as a noted absence", () => {
  render(<JudgementCard judgement={null} />);

  expect(screen.getByText("该运行没有裁判记录。")).toBeTruthy();
});

test("renders an unreadable judgement as a warning, never a crash", () => {
  render(<JudgementCard judgement={null} note="unreadable judgement record" />);

  expect(screen.getByText("裁判记录无法读取")).toBeTruthy();
  expect(screen.getByText("unreadable judgement record")).toBeTruthy();
});
