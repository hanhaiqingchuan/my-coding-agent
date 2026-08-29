import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { CampaignDetailDto } from "./types";
import { EvaluationDetail } from "./EvaluationDetail";

afterEach(cleanup);

import fixture from "./fixtures/evaluation.fixture.json";

const DETAIL = fixture.campaignDetail as unknown as CampaignDetailDto;

test("renders one row per task with the deterministic metric columns", () => {
  render(<EvaluationDetail detail={DETAIL} onOpenRun={vi.fn()} />);

  expect(screen.getByText("demo-task")).toBeTruthy();
  expect(screen.getByText("second-task")).toBeTruthy();
  // Deterministic metrics: rounds, tool calls, tokens, duration, stop reason.
  expect(screen.getAllByLabelText(/^状态/).map((c) => c.textContent)).toEqual([
    "COMPLETED",
    "COMPLETED",
  ]);
  expect(
    screen.getAllByLabelText(/^停止原因/).map((c) => c.textContent),
  ).toEqual(["COMPLETED", "COMPLETED"]);
  expect(screen.getAllByLabelText(/^轮次/).length).toBe(2);
  const rounds = screen.getAllByLabelText(/^轮次/) as HTMLElement[];
  expect(rounds.map((cell) => cell.textContent)).toEqual(["2", "2"]);
  expect(screen.getAllByLabelText(/^工具调用/).length).toBe(2);
  const tokens = screen
    .getAllByLabelText(/^输入 Token/)
    .map((cell) => cell.textContent);
  expect(tokens).toEqual(["30", "30"]);
  const output = screen
    .getAllByLabelText(/^输出 Token/)
    .map((cell) => cell.textContent);
  expect(output).toEqual(["9", "9"]);
  const durations = screen
    .getAllByLabelText(/^耗时/)
    .map((cell) => cell.textContent);
  expect(durations).toEqual(["11 ms", "11 ms"]);
  // Strict-success and artifact badges.
  expect(screen.getAllByLabelText(/^严格成功/).length).toBe(2);
  expect(screen.getAllByText("通过").length).toBe(2);
  expect(screen.getAllByText("正确").length).toBe(2);
  // Judge mini-scores, one triple per judged run.
  expect(screen.getAllByLabelText(/裁判评分/).length).toBe(2);
  expect(screen.getAllByText("4 · 5 · 3").length).toBe(2);
});

test("shows the campaign aggregates beside the task rows", () => {
  render(<EvaluationDetail detail={DETAIL} onOpenRun={vi.fn()} />);

  expect(screen.getByText("严格成功")).toBeTruthy();
  expect(screen.getByText("2 次有效运行中通过 2")).toBeTruthy();
  expect(screen.getByText("Token 输入 60 · 输出 18")).toBeTruthy();
  expect(screen.getByText("主请求 4 · 工具调用 2")).toBeTruthy();
  expect(screen.getByText("已评判 2 · 裁判错误 0")).toBeTruthy();
});

test("flags failed strict-success rows and judge errors distinctly", () => {
  const detail: CampaignDetailDto = {
    ...DETAIL,
    tasks: [
      {
        task_id: "failing-task",
        category: "local_edit",
        runs: [
          {
            ...DETAIL.tasks[0].runs[0],
            task_id: "failing-task",
            strict_success: false,
            artifact_correct: false,
            stop_reason: "MAX_ROUNDS",
            failure_kind: "max_rounds",
            judge_scores: null,
            judge_error: true,
          },
        ],
      },
    ],
  };

  render(<EvaluationDetail detail={detail} onOpenRun={vi.fn()} />);

  expect(screen.getByText("未通过")).toBeTruthy();
  expect(screen.getByText("错误")).toBeTruthy();
  expect(screen.getByText("MAX_ROUNDS")).toBeTruthy();
  expect(screen.getByText("裁判错误")).toBeTruthy();
  expect(screen.queryByText("4 · 5 · 3")).toBeNull();
});

test("renders a corrupt campaign as a noted empty state", () => {
  const detail: CampaignDetailDto = {
    ...DETAIL,
    summary: {
      ...DETAIL.summary,
      corrupt: true,
      note: "unreadable runs.jsonl",
    },
    aggregates: null,
    tasks: [],
  };

  render(<EvaluationDetail detail={detail} onOpenRun={vi.fn()} />);

  expect(screen.getByText("已损坏")).toBeTruthy();
  expect(screen.getByText("unreadable runs.jsonl")).toBeTruthy();
  expect(screen.queryByText("demo-task")).toBeNull();
});
