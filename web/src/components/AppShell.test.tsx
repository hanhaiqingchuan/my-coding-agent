import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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

  expect(
    screen.getByRole("navigation", { name: "会话与工作区" })
      .textContent,
  ).toContain("Sessions");
  expect(
    screen.getByRole("main", { name: "对话" }).textContent,
  ).toContain("Conversation");
  expect(
    screen.getByRole("complementary", { name: "运行详情" }).textContent,
  ).toContain("Idle");
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

  await user.click(screen.getByRole("button", { name: "打开运行详情" }));

  expect(
    screen.getByRole("dialog", { name: "运行详情" }).textContent,
  ).toContain("Idle");
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

  await user.click(screen.getByRole("button", { name: "打开运行详情" }));

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
  const trigger = screen.getByRole("button", { name: "打开运行详情" });

  await user.click(trigger);

  const drawer = screen.getByRole("dialog", { name: "运行详情" });
  const close = within(drawer).getByRole("button", {
    name: "关闭运行详情",
  });
  const inspect = within(drawer).getByRole("button", { name: "Inspect run" });
  expect(document.activeElement).toBe(close);
  await user.tab();
  expect(document.activeElement).toBe(inspect);
  await user.tab();
  expect(document.activeElement).toBe(close);
  await user.tab({ shift: true });
  expect(document.activeElement).toBe(inspect);

  await user.keyboard("{Escape}");

  expect(screen.queryByRole("dialog", { name: "运行详情" })).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

test("each rail collapses to a strip and expands back with its content intact", async () => {
  const user = userEvent.setup();
  render(
    <AppShell
      sidebar={<p>Sessions</p>}
      conversation={<p>Conversation</p>}
      runDetails={<p>Idle</p>}
    />,
  );

  const detailsRail = screen.getByRole("complementary", { name: "运行详情" });
  const detailsToggle = within(detailsRail).getByRole("button", {
    name: "»",
  });
  expect(detailsToggle.getAttribute("aria-expanded")).toBe("true");

  await user.click(detailsToggle);
  expect(detailsRail.getAttribute("data-collapsed")).toBe("true");
  expect(detailsToggle.getAttribute("aria-expanded")).toBe("false");
  expect(within(detailsRail).queryByText("Idle")).toBeNull();
  // The rail keeps its heading region so screen-reader users still find it.
  expect(screen.getByRole("complementary", { name: "运行详情" })).toBe(
    detailsRail,
  );

  await user.click(within(detailsRail).getByRole("button", { name: "«" }));
  expect(detailsRail.getAttribute("data-collapsed")).toBe("false");
  expect(within(detailsRail).getByText("Idle")).not.toBeNull();

  const sidebar = screen.getByRole("navigation", { name: "会话与工作区" });
  await user.click(within(sidebar).getByRole("button", { name: "«" }));
  expect(sidebar.getAttribute("data-collapsed")).toBe("true");
  expect(within(sidebar).queryByText("Sessions")).toBeNull();
});

test("dragging a rail edge resizes its track within the clamped bounds", () => {
  render(
    <AppShell
      sidebar={<p>Sessions</p>}
      conversation={<p>Conversation</p>}
      runDetails={<p>Idle</p>}
    />,
  );
  const shell = document.querySelector<HTMLElement>(".app-shell");
  expect(shell).not.toBeNull();
  const handle = screen.getByRole("separator", { name: "调整运行详情栏宽度" });

  fireEvent.mouseDown(handle, { clientX: 500 });
  fireEvent.mouseMove(window, { clientX: 420 });
  fireEvent.mouseUp(window);
  // Dragging the right rail's left edge leftwards widens it: 320 + 80 = 400.
  expect((shell as HTMLElement).style.getPropertyValue("--details-track")).toBe(
    "400px",
  );

  const sidebarHandle = screen.getByRole("separator", {
    name: "调整会话栏宽度",
  });
  fireEvent.mouseDown(sidebarHandle, { clientX: 100 });
  fireEvent.mouseMove(window, { clientX: 40 });
  fireEvent.mouseUp(window);
  // The sidebar's right edge moved 60px leftwards: 264 - 60 = 204.
  expect((shell as HTMLElement).style.getPropertyValue("--sidebar-track")).toBe(
    "204px",
  );

  // Clamped: a huge drag cannot push the track past its maximum.
  fireEvent.mouseDown(sidebarHandle, { clientX: 40 });
  fireEvent.mouseMove(window, { clientX: 4_000 });
  fireEvent.mouseUp(window);
  expect((shell as HTMLElement).style.getPropertyValue("--sidebar-track")).toBe(
    "420px",
  );
});

test("the resize separators answer the arrow keys for keyboard users", async () => {
  const user = userEvent.setup();
  render(
    <AppShell
      sidebar={<p>Sessions</p>}
      conversation={<p>Conversation</p>}
      runDetails={<p>Idle</p>}
    />,
  );
  const shell = document.querySelector<HTMLElement>(".app-shell");
  const handle = screen.getByRole("separator", { name: "调整运行详情栏宽度" });

  handle.focus();
  await user.keyboard("{ArrowLeft}");
  expect((shell as HTMLElement).style.getPropertyValue("--details-track")).toBe(
    "336px",
  );
  await user.keyboard("{ArrowRight}");
  expect((shell as HTMLElement).style.getPropertyValue("--details-track")).toBe(
    "320px",
  );
});
