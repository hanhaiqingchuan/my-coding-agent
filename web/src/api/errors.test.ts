import { afterEach, expect, test } from "vitest";
import { cleanup } from "@testing-library/react";

import { describeStopOutcome, failureBannerFor } from "./errors";
import { STOP_REASONS, type RunDto, type RunTotalsDto } from "./types";

afterEach(cleanup);

/** Every stop reason the API contract defines must map to a friendly record. */
test("describes every stop reason in the API contract in Chinese", () => {
  for (const code of STOP_REASONS) {
    const outcome = describeStopOutcome(code, null);
    expect(outcome, code).not.toBeNull();
    expect(outcome?.code, code).toBe(code);
    expect(outcome?.title.length, code).toBeGreaterThan(0);
    expect(outcome?.description.length, code).toBeGreaterThan(0);
    // A hint is required for every outcome the user could act on.
    if (outcome?.isError) {
      expect(outcome.hint, code).not.toBeNull();
    }
  }
});

test("keeps the neutral outcomes separate from the error outcomes", () => {
  expect(describeStopOutcome("completed", null)?.isError).toBe(false);
  expect(describeStopOutcome("user_stop", null)?.isError).toBe(false);
  expect(describeStopOutcome("pause_turn", null)?.isError).toBe(false);
  for (const code of [
    "max_rounds",
    "doom_loop",
    "empty_response",
    "output_truncated",
    "incomplete_tool_call",
    "auth_error",
    "config_error",
    "retry_exhausted",
    "context_overflow",
    "model_refusal",
    "server_restart",
    "model_protocol_error",
    "internal_error",
  ]) {
    expect(describeStopOutcome(code, null)?.isError, code).toBe(true);
  }
});

test.each([
  ["auth_error", "鉴权", /密钥/, /密钥|环境变量/],
  ["retry_exhausted", "重试", /限流|服务/, /稍后|重试/],
  ["context_overflow", "上下文", /上下文/, /新会话|窗口/],
  ["model_protocol_error", "协议", /协议/, /.+/],
  ["internal_error", "内部错误", /内部/, /运行 ID|重试/],
  ["output_truncated", "截断", /截断/, /继续|token/],
])(
  "describes %s with a title, a cause and an actionable hint",
  (code, titleFragment, descriptionPattern, hintPattern) => {
    const outcome = describeStopOutcome(code, null);
    expect(outcome?.title).toContain(titleFragment);
    expect(outcome?.description).toMatch(descriptionPattern);
    expect(outcome?.hint).toMatch(hintPattern);
  },
);

test("returns null when the run has neither a stop reason nor an error kind", () => {
  expect(describeStopOutcome(null, null)).toBeNull();
});

test("falls back to the error kind when the stop reason is unknown", () => {
  const outcome = describeStopOutcome("something_new", "auth_error");
  expect(outcome?.code).toBe("auth_error");
  expect(outcome?.title).toContain("鉴权");
});

test("falls back gracefully for an unknown code by keeping the raw value", () => {
  const outcome = describeStopOutcome("totally_unknown", null);
  expect(outcome).not.toBeNull();
  expect(outcome?.isError).toBe(true);
  expect(outcome?.code).toBe("totally_unknown");
  expect(outcome?.title.length).toBeGreaterThan(0);
  expect(outcome?.description.length).toBeGreaterThan(0);
  expect(outcome?.hint).not.toBeNull();
});

test("a live run without a stop reason yields no banner", () => {
  expect(failureBannerFor(liveRun())).toBeNull();
});

test("a failed run yields a banner that carries the retry fact", () => {
  const banner = failureBannerFor(finishedRun({ state: "failed" }));
  expect(banner).not.toBeNull();
  expect(banner?.runId).toBe("run-1");
  expect(banner?.title).toBe(
    describeStopOutcome("retry_exhausted", null)?.title,
  );
  expect(banner?.description).toContain("限流");
  expect(banner?.hint).not.toBeNull();
  expect(banner?.retryCount).toBe(3);
});

test("a failed run with zero retries reports no retry count", () => {
  const banner = failureBannerFor(
    finishedRun({
      state: "failed",
      totals: { ...ZERO_TOTALS, retry_count: 0 },
    }),
  );
  expect(banner?.retryCount).toBe(0);
});

test("a user stop never yields a banner", () => {
  expect(
    failureBannerFor(
      finishedRun({ state: "cancelled", stop_reason: "user_stop" }),
    ),
  ).toBeNull();
});

test("a completed run never yields a banner", () => {
  expect(
    failureBannerFor(
      finishedRun({ state: "completed", stop_reason: "completed" }),
    ),
  ).toBeNull();
});

test("an unknown failure code still yields a banner with the raw code", () => {
  // Spec 5.2 fixes a minimum stop-reason vocabulary, not a closed one, so the
  // cast stands in for a value the contract does not name yet.
  const banner = failureBannerFor(
    finishedRun({
      state: "failed",
      stop_reason: "totally_unknown" as RunDto["stop_reason"],
      error_kind: null,
    }),
  );
  expect(banner).not.toBeNull();
  expect(banner?.code).toBe("totally_unknown");
  expect(banner?.title).toBe("运行异常结束");
  expect(banner?.hint).not.toBeNull();
});

test("a null run never yields a banner", () => {
  expect(failureBannerFor(null)).toBeNull();
});

const ZERO_TOTALS: RunTotalsDto = {
  input_tokens: 0,
  output_tokens: 0,
  cache_creation_input_tokens: 0,
  cache_read_input_tokens: 0,
  round_count: 0,
  retry_count: 0,
};

function liveRun(overrides: Partial<RunDto> = {}): RunDto {
  return {
    id: "run-1",
    session_id: "session-1",
    state: "model_streaming",
    stop_reason: null,
    error_kind: null,
    cancellation_requested_at: null,
    config_snapshot: {},
    started_at: "2026-08-28T00:00:00Z",
    finished_at: null,
    totals: ZERO_TOTALS,
    context: null,
    ...overrides,
  };
}

function finishedRun(overrides: Partial<RunDto> = {}): RunDto {
  return liveRun({
    state: "failed",
    stop_reason: "retry_exhausted",
    error_kind: "retry_exhausted",
    finished_at: "2026-08-28T00:04:00Z",
    totals: { ...ZERO_TOTALS, retry_count: 3, round_count: 2 },
    ...overrides,
  });
}
