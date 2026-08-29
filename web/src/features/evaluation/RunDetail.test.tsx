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
    screen.getByRole("heading", { name: "demo-task · repeat 1" }),
  ).toBeTruthy();
  expect(fact(/^State$/)).toBe("COMPLETED");
  expect(fact(/^Stop reason$/)).toBe("COMPLETED");
  expect(fact(/^Outcome$/)).toBe("OK");
  expect(fact(/^Rounds$/)).toBe("2");
  expect(fact(/^Input tokens$/)).toBe("30");
  expect(fact(/^Output tokens$/)).toBe("9");
  expect(fact(/^Tool calls$/)).toBe("1 of 1 proposed");
  expect(fact(/^Target oracle$/)).toBe("passed");
  expect(fact(/^Regression oracle$/)).toBe("passed");
  expect(fact(/^Agent commit$/)).toBe("0f1e2d3c");
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
    name: /^Rationale · \d+ chars$/,
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
  expect(screen.queryByText(/^State$/)).toBeNull();
});

test("renders a missing judgement as a noted absence", () => {
  render(
    <RunDetail detail={{ ...RUN, judgement: null, judgement_note: null }} />,
  );

  expect(screen.getByText("No judgement record for this run.")).toBeTruthy();
});
