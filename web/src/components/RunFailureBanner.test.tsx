import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";

import type { RunFailure } from "../api/errors";
import { RunFailureBanner } from "./RunFailureBanner";

afterEach(cleanup);

const EXHAUSTED: RunFailure = {
  runId: "run-1",
  code: "retry_exhausted",
  title: "请求重试用尽",
  description:
    "模型服务持续不可用（可能是限流 429、服务端 5xx 或网络波动），自动重试后仍然失败。",
  hint: "稍等片刻后重新发送消息即可重试；若持续失败请检查网络或模型服务状态。",
  retryCount: 3,
};

test("shows the friendly title, cause, retry fact and hint of a failed run", () => {
  render(<RunFailureBanner failure={EXHAUSTED} />);

  expect(screen.getByText("请求重试用尽")).not.toBeNull();
  expect(screen.getByText(/限流 429/)).not.toBeNull();
  expect(screen.getByText("已自动重试 3 次。")).not.toBeNull();
  expect(screen.getByText(/稍等片刻后重新发送/)).not.toBeNull();
});

test("omits the retry fact when the run never retried", () => {
  render(<RunFailureBanner failure={{ ...EXHAUSTED, retryCount: 0 }} />);

  expect(screen.queryByText(/已自动重试/)).toBeNull();
});

test("a failure without a hint renders without an empty hint row", () => {
  render(<RunFailureBanner failure={{ ...EXHAUSTED, hint: null }} />);

  expect(screen.queryByText(/稍等片刻/)).toBeNull();
});

test("dismisses the banner on request and keeps it dismissed for the same run", async () => {
  const user = userEvent.setup();
  const { rerender } = render(<RunFailureBanner failure={EXHAUSTED} />);

  await user.click(screen.getByRole("button", { name: "关闭提示" }));
  expect(screen.queryByText("请求重试用尽")).toBeNull();

  // The same run keeps its dismissal across snapshot refreshes.
  rerender(<RunFailureBanner failure={{ ...EXHAUSTED } as const} />);
  expect(screen.queryByText("请求重试用尽")).toBeNull();
});

test("shows the banner again when a different run fails", async () => {
  const user = userEvent.setup();
  const { rerender } = render(<RunFailureBanner failure={EXHAUSTED} />);

  await user.click(screen.getByRole("button", { name: "关闭提示" }));
  rerender(<RunFailureBanner failure={{ ...EXHAUSTED, runId: "run-2" }} />);

  expect(screen.getByText("请求重试用尽")).not.toBeNull();
});

test("renders an unmapped failure with the generic fallback instead of nothing", () => {
  render(
    <RunFailureBanner
      failure={{
        runId: "run-9",
        code: "totally_unknown",
        title: "运行异常结束",
        description: "运行以未知原因结束。",
        hint: "可以重新发送消息重试；若持续出现请查看服务端日志。",
        retryCount: 0,
      }}
    />,
  );

  // The banner stays compact; the run-details panel quotes the raw code.
  expect(screen.getByText("运行异常结束")).not.toBeNull();
  expect(screen.getByText("运行以未知原因结束。")).not.toBeNull();
  expect(screen.getByText(/可以重新发送消息重试/)).not.toBeNull();
});
