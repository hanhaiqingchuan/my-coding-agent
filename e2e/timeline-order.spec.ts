import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";

const BACKEND_URL = "http://127.0.0.1:8000";

/** Text content of every timeline entry, in the order the browser renders it. */
async function timelineEntries(page: Page): Promise<string[]> {
  return await page
    .locator(".timeline-scroll > article")
    .evaluateAll((entries) => entries.map((entry) => entry.textContent ?? ""));
}

test("interleaves multi-round tool cards with their assistant messages in server order", async ({
  page,
  request,
}) => {
  const stateResponse = await request.get(`${BACKEND_URL}/__test__/state`);
  expect(stateResponse.ok()).toBe(true);
  const state = (await stateResponse.json()) as { workspace: string };

  await page.goto("/");
  await page.getByRole("button", { name: "创建新会话" }).click();
  await page.getByRole("textbox", { name: "工作区" }).fill(state.workspace);
  await page.getByLabel(/会话名称/).fill("Timeline order");
  await page.getByRole("button", { name: "打开工作区" }).click();
  await expect(
    page.getByRole("heading", { name: "Timeline order" }),
  ).toBeVisible();

  await page.getByRole("textbox", { name: "消息" }).fill("timeline-flow");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("Round one prepares two effects.")).toBeVisible();

  const approval = page.getByRole("region", { name: "待审批" });
  const approvalValue = (label: string) =>
    approval
      .locator("dl > div")
      .filter({ has: page.getByText(label, { exact: true }) })
      .getByRole("definition");

  await expect(
    approval.getByRole("heading", { name: "write_file" }),
  ).toBeVisible();
  await approval.getByRole("button", { name: "批准" }).click();

  // The model sent only `command`; cwd and timeout exist solely as the effective values
  // run_command froze into the approval metadata.
  await expect(
    approval.getByRole("heading", { name: "run_command" }),
  ).toBeVisible();
  await expect(approvalValue("工作目录")).toHaveText(
    /^\/.+\/workspace$/,
  );
  await expect(approvalValue("超时")).toHaveText("120s");
  await approval.screenshot({
    path: "test-results/command-approval-effective-values.png",
  });
  await approval.getByRole("button", { name: "批准" }).click();

  await expect(
    page.getByText("Round two continues after the first round."),
  ).toBeVisible();
  await expect(
    approval.getByRole("heading", { name: "write_file" }),
  ).toBeVisible();
  await approval.getByRole("button", { name: "批准" }).click();

  await expect(page.getByText("All timeline steps completed.")).toBeVisible();
  await expect(page.getByRole("button", { name: "发送" })).toBeVisible();

  // Round one asked for two calls and round two for one, so call_order runs 0, 1, 0.
  // Ordering by call_order would pull round two's card ahead of round one's second card,
  // and rendering all messages before all cards would push both cards past every message.
  const entries = await timelineEntries(page);
  const position = (needle: string) => {
    const index = entries.findIndex((entry) => entry.includes(needle));
    expect(index, `timeline entry containing ${needle}`).toBeGreaterThanOrEqual(
      0,
    );
    return index;
  };
  expect(position("Round one prepares")).toBeLessThan(position("alpha"));
  expect(position("alpha")).toBeLessThan(position("timeline-marker.txt"));
  expect(position("timeline-marker.txt")).toBeLessThan(
    position("Round two continues"),
  );
  expect(position("Round two continues")).toBeLessThan(position("beta"));

  const toolCards = page.locator("article.tool-card");
  await expect(toolCards).toHaveCount(3);
  const replaceCard = toolCards.nth(2);
  await expect(replaceCard).toHaveAccessibleName("write_file 成功");
  await replaceCard.getByText("详情").click();
  await expect(replaceCard.getByText("替换后文本")).toBeVisible();
  await expect(replaceCard.getByText("beta")).toBeVisible();
  await replaceCard.screenshot({
    path: "test-results/write-file-replace-card.png",
  });

  expect(readFileSync(join(state.workspace, "timeline-a.txt"), "utf8")).toBe(
    "beta\n",
  );
  expect(
    readFileSync(join(state.workspace, "timeline-marker.txt"), "utf8"),
  ).toBe("verified");
});
