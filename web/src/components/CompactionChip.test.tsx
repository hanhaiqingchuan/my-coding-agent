import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { CompactionChip } from "./CompactionChip";

afterEach(cleanup);

test("shows the running statement while the compactor works", () => {
  render(<CompactionChip status={{ phase: "running" }} />);
  expect(screen.getByRole("status").textContent).toBe("正在压缩上下文…");
});

test("reports the before and after estimates once compaction finishes", () => {
  render(
    <CompactionChip
      status={{ phase: "finished", beforeTokens: 61_440, afterTokens: 33_200 }}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe(
    "上下文已压缩：61,440 → 33,200 tokens",
  );
});

test("renders nothing without a compaction lifecycle", () => {
  const { container } = render(<CompactionChip status={null} />);
  expect(container.textContent).toBe("");
});

test("a failed compaction reports the failure instead of a success line", () => {
  render(
    <CompactionChip
      status={{
        phase: "finished",
        beforeTokens: 805,
        afterTokens: 805,
        errorCode: "INVALID_SUMMARY_STRUCTURE",
      }}
    />,
  );
  expect(screen.getByRole("status").textContent).toContain("压缩失败");
  expect(screen.getByRole("status").textContent).toContain(
    "INVALID_SUMMARY_STRUCTURE",
  );
  expect(screen.getByRole("status").textContent).not.toContain("上下文已压缩");
});
