import { cleanup, render, screen, within } from "@testing-library/react";
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

test("drawer manages focus, traps Tab, and restores focus after Escape", async () => {
  const user = userEvent.setup();
  render(
    <AppShell
      sidebar={<p>Sessions</p>}
      conversation={<p>Conversation</p>}
      runDetails={<button type="button">Inspect run</button>}
    />,
  );
  const trigger = screen.getByRole("button", { name: "Open run details" });

  await user.click(trigger);

  const drawer = screen.getByRole("dialog", { name: "Run details" });
  const close = within(drawer).getByRole("button", { name: "Close run details" });
  const inspect = within(drawer).getByRole("button", { name: "Inspect run" });
  expect(document.activeElement).toBe(close);
  await user.tab();
  expect(document.activeElement).toBe(inspect);
  await user.tab();
  expect(document.activeElement).toBe(close);
  await user.tab({ shift: true });
  expect(document.activeElement).toBe(inspect);

  await user.keyboard("{Escape}");

  expect(screen.queryByRole("dialog", { name: "Run details" })).toBeNull();
  expect(document.activeElement).toBe(trigger);
});
