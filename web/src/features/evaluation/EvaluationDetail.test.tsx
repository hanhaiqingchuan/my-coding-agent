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
  expect(screen.getAllByLabelText(/^State/).map((c) => c.textContent)).toEqual([
    "COMPLETED",
    "COMPLETED",
  ]);
  expect(
    screen.getAllByLabelText(/^Stop reason/).map((c) => c.textContent),
  ).toEqual(["COMPLETED", "COMPLETED"]);
  expect(screen.getAllByLabelText(/^Rounds/).length).toBe(2);
  const rounds = screen.getAllByLabelText(/^Rounds/) as HTMLElement[];
  expect(rounds.map((cell) => cell.textContent)).toEqual(["2", "2"]);
  expect(screen.getAllByLabelText(/^Tool calls/).length).toBe(2);
  const tokens = screen
    .getAllByLabelText(/^Input tokens/)
    .map((cell) => cell.textContent);
  expect(tokens).toEqual(["30", "30"]);
  const output = screen
    .getAllByLabelText(/^Output tokens/)
    .map((cell) => cell.textContent);
  expect(output).toEqual(["9", "9"]);
  const durations = screen
    .getAllByLabelText(/^Duration/)
    .map((cell) => cell.textContent);
  expect(durations).toEqual(["11 ms", "11 ms"]);
  // Strict-success and artifact badges.
  expect(screen.getAllByLabelText(/^Strict success/).length).toBe(2);
  expect(screen.getAllByText("pass").length).toBe(2);
  expect(screen.getAllByText("ok").length).toBe(2);
  // Judge mini-scores, one triple per judged run.
  expect(screen.getAllByLabelText(/judge scores/i).length).toBe(2);
  expect(screen.getAllByText("4 · 5 · 3").length).toBe(2);
});

test("shows the campaign aggregates beside the task rows", () => {
  render(<EvaluationDetail detail={DETAIL} onOpenRun={vi.fn()} />);

  expect(screen.getByText("Strict success")).toBeTruthy();
  expect(screen.getByText("2 / 2 valid runs")).toBeTruthy();
  expect(screen.getByText("Tokens 60 in · 18 out")).toBeTruthy();
  expect(screen.getByText("4 main requests · 2 tool calls")).toBeTruthy();
  expect(screen.getByText("2 judged · 0 judge errors")).toBeTruthy();
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

  expect(screen.getByText("fail")).toBeTruthy();
  expect(screen.getByText("wrong")).toBeTruthy();
  expect(screen.getByText("MAX_ROUNDS")).toBeTruthy();
  expect(screen.getByText("judge error")).toBeTruthy();
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

  expect(screen.getByText("corrupt")).toBeTruthy();
  expect(screen.getByText("unreadable runs.jsonl")).toBeTruthy();
  expect(screen.queryByText("demo-task")).toBeNull();
});
