import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";

import type { RunDetailDto } from "./types";
import { RunDetail } from "./RunDetail";

afterEach(cleanup);

import fixture from "./fixtures/evaluation.fixture.json";

const RUN = fixture.runDetail as unknown as RunDetailDto;

function fact(label: RegExp): string | undefined {
  const cell = screen
    .getAllByText(label)
    .map((node) => node.parentElement?.querySelector("dd"))
    .find((value) => value !== null);
  return cell?.textContent ?? undefined;
}

test("renders the run-v1 facts the dashboard promises", () => {
  render(<RunDetail detail={RUN} />);

  expect(
    screen.getByRole("heading", { name: "demo-task · 第 1 次" }),
  ).toBeTruthy();
  expect(fact(/^状态$/)).toBe("COMPLETED");
  expect(fact(/^停止原因$/)).toBe("COMPLETED");
  expect(fact(/^结果$/)).toBe("OK");
  expect(fact(/^轮次$/)).toBe("2");
  expect(fact(/^输入 Token$/)).toBe("30");
  expect(fact(/^输出 Token$/)).toBe("9");
  expect(fact(/^工具调用$/)).toBe("已执行 1 / 共提议 1");
  expect(fact(/^目标校验$/)).toBe("通过");
  expect(fact(/^回归校验$/)).toBe("通过");
  expect(fact(/^智能体提交$/)).toBe("0f1e2d3c");
  // The exported final assistant message reads as the run's own summary.
  expect(
    screen.getByText(
      "Created the requested helper and verified it with the existing suite.",
    ),
  ).toBeTruthy();
});

test("renders the judgement card with its rationale collapsed", async () => {
  const user = userEvent.setup();
  render(<RunDetail detail={RUN} />);

  const toggle = screen.getByRole("button", {
    name: /^裁判理由 · \d+ 字$/,
  });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");

  await user.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText(/The goal was met with tidy tool use/)).toBeTruthy();
});

test("degrades to a note when the run document is unreadable", () => {
  render(
    <RunDetail
      detail={{
        ...RUN,
        run: null,
        run_note: "unreadable run record for task demo-task repeat 1",
      }}
    />,
  );

  expect(
    screen.getByText("unreadable run record for task demo-task repeat 1"),
  ).toBeTruthy();
  expect(screen.queryByText(/^状态$/)).toBeNull();
});

test("renders a missing judgement as a noted absence", () => {
  render(
    <RunDetail detail={{ ...RUN, judgement: null, judgement_note: null }} />,
  );

  expect(screen.getByText("该运行没有裁判记录。")).toBeTruthy();
});
