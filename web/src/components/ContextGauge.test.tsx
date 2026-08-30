import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import type { RunContextDto } from "../api/types";
import { ContextGauge } from "./ContextGauge";

afterEach(cleanup);

function context(
  estimated: number,
  available: number,
  windowTokens = available,
): RunContextDto {
  return {
    estimated_tokens: estimated,
    available_tokens: available,
    window_tokens: windowTokens,
  };
}

test("renders the evidence-based percentage from the builder estimate", () => {
  render(<ContextGauge context={context(12_000, 60_000)} />);

  const gauge = screen.getByRole("status", { name: "上下文占用" });
  expect(gauge.textContent).toContain("20%");
  expect(gauge.textContent).toContain("12,000 / 60,000 tokens");
  // Under 60% the bar stays in its calm level.
  expect(gauge.querySelector(".context-gauge-ok")).not.toBeNull();
  expect(gauge.querySelector(".context-gauge-warn")).toBeNull();
  expect(gauge.querySelector(".context-gauge-high")).toBeNull();
});

test("the level steps up at the compaction thresholds", () => {
  const { rerender } = render(<ContextGauge context={context(45_000, 60_000)} />);
  expect(
    screen.getByRole("status", { name: "上下文占用" }).querySelector(
      ".context-gauge-warn",
    ),
  ).not.toBeNull();

  rerender(<ContextGauge context={context(55_000, 60_000)} />);
  expect(
    screen.getByRole("status", { name: "上下文占用" }).querySelector(
      ".context-gauge-high",
    ),
  ).not.toBeNull();
});

test("renders nothing without a recorded estimate rather than inventing a figure", () => {
  const { container } = render(<ContextGauge context={null} />);
  expect(container.textContent).toBe("");
});

test("renders nothing when the backend reports a degenerate window", () => {
  const { container } = render(
    <ContextGauge context={context(0, 0)} />,
  );
  expect(container.textContent).toBe("");
});

test("caps the fill at one hundred percent", () => {
  render(<ContextGauge context={context(90_000, 60_000)} />);
  const fill = screen
    .getByRole("status", { name: "上下文占用" })
    .querySelector<HTMLElement>(".context-gauge-fill");
  expect(fill?.getAttribute("style")).toContain("width: 100%");
});
