import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { AppShell } from "./AppShell";

afterEach(cleanup);

test("renders semantic session, conversation, and run-details regions", () => {
  render(
    <AppShell
      sidebar={<p>Sessions</p>}
      conversation={<p>Conversation</p>}
      runDetails={<p>Idle</p>}
    />,
  );

  expect(screen.getByRole("navigation", { name: "Sessions and workspace" }).textContent).toContain(
    "Sessions",
  );
  expect(screen.getByRole("main", { name: "Conversation" }).textContent).toContain("Conversation");
  expect(screen.getByRole("complementary", { name: "Run details" }).textContent).toContain("Idle");
});

test("opens run details in an accessible drawer on a narrow layout", async () => {
  const user = userEvent.setup();
  render(
    <AppShell
      sidebar={<p>Sessions</p>}
      conversation={<p>Conversation</p>}
      runDetails={<p>Idle</p>}
    />,
  );

  await user.click(screen.getByRole("button", { name: "Open run details" }));

  expect(screen.getByRole("dialog", { name: "Run details" }).textContent).toContain("Idle");
});

test("does not infer a completed run from a shell interaction", async () => {
  const user = userEvent.setup();
  const onDetailsDrawerChange = vi.fn();
  render(
    <AppShell
      sidebar={<p>Sessions</p>}
      conversation={<p>Conversation</p>}
      runDetails={<p>model_streaming</p>}
      onDetailsDrawerChange={onDetailsDrawerChange}
    />,
  );

  await user.click(screen.getByRole("button", { name: "Open run details" }));

  expect(screen.getAllByText("model_streaming")).toHaveLength(2);
  expect(onDetailsDrawerChange).toHaveBeenCalledWith(true);
});
