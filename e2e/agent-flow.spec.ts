import { expect, test } from "@playwright/test";

const BACKEND_URL = "http://127.0.0.1:8000";

test("creates a session, approves write and command, then restores final history", async ({
  page,
  request,
}) => {
  const stateResponse = await request.get(`${BACKEND_URL}/__test__/state`);
  expect(stateResponse.ok()).toBe(true);
  const state = (await stateResponse.json()) as { workspace: string };

  // A unique title keeps the heading assertion below tied to THIS session: the
  // app auto-selects a previous session on load, and the create call only lands
  // after its CSRF bootstrap roundtrip.
  const sessionTitle = `Complete flow ${Date.now()}`;

  await page.goto("/");
  await page.getByRole("textbox", { name: "工作区" }).fill(state.workspace);
  await page.getByLabel(/会话名称/).fill(sessionTitle);
  await page.getByRole("button", { name: "打开工作区" }).click();
  await expect(
    page.getByRole("heading", { name: sessionTitle }),
  ).toBeVisible();

  await page.getByRole("textbox", { name: "消息" }).fill("agent-flow");
  await page.getByRole("button", { name: "发送" }).click();

  // Round one streams a thinking block before its text: the box renders expanded
  // while the reasoning arrives, then auto-collapses when the block closes. The
  // scripted block holds open until released, so the mid-stream check is exact.
  const thinkingToggle = page.getByRole("button", {
    name: /^思考中 · \d+ 字$/,
  });
  const thinkingText = page.locator(".thinking-text");
  await expect(thinkingToggle).toHaveAttribute("aria-expanded", "true");
  await expect(thinkingText).toContainText("prepare a workspace change");
  await page.screenshot({
    path: "test-results/thinking-live-expanded.png",
  });

  await request.post(`${BACKEND_URL}/__test__/thinking/release`);
  // A closed block reads as finished reasoning, so the live locator goes stale.
  const closedThinkingToggle = page.getByRole("button", {
    name: /^思考完成 · \d+ 字$/,
  });
  await expect(closedThinkingToggle).toHaveAttribute("aria-expanded", "false");
  await expect(thinkingText).toBeHidden();
  await page.screenshot({
    path: "test-results/thinking-live-collapsed.png",
  });

  await expect(page.getByText("Preparing the workspace change…")).toBeVisible();

  const runDetails = page.getByRole("complementary", { name: "运行详情" });
  const detailValue = (label: string) =>
    runDetails
      .locator("dl > div")
      .filter({ has: page.getByText(label, { exact: true }) })
      .getByRole("definition");

  const writeApproval = page.getByRole("region", { name: "待审批" });
  await expect(
    writeApproval.getByRole("heading", { name: "write_file" }),
  ).toBeVisible();
  await expect(detailValue("模型")).toHaveText("scripted-e2e");
  await expect(detailValue("轮次")).toHaveText("1");
  await writeApproval.getByRole("button", { name: "批准" }).click();

  await expect(
    writeApproval.getByRole("heading", { name: "run_command" }),
  ).toBeVisible();
  await expect(detailValue("轮次")).toHaveText("2");
  await expect(detailValue("重试")).toHaveText("0");
  await expect(detailValue("累计 Token")).toContainText(
    "输入 16 · 输出 16 · 缓存写入 0 · 缓存读取 0",
  );
  await expect(
    writeApproval.getByText(
      "test -f agent-output.txt && printf verified > command-marker.txt",
    ),
  ).toBeVisible();
  await expect(writeApproval.getByText("10s")).toBeVisible();
  // The non-sandbox warning line was dropped by the UX wave: the frozen facts
  // (command, cwd, reason, timeout) stay, and no 警告 row renders.
  await expect(writeApproval.getByText("警告")).toHaveCount(0);
  await writeApproval.getByRole("button", { name: "批准" }).click();

  await expect(page.getByText("All scripted steps completed.")).toBeVisible();
  await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
  await expect(page.getByLabel("write_file 成功")).toBeVisible();
  await expect(page.getByLabel("run_command 成功")).toBeVisible();

  await page.reload();
  await expect(page.getByText("All scripted steps completed.")).toBeVisible();
  await expect(page.getByRole("button", { name: "发送" })).toBeVisible();

  // The committed round-one message keeps its reasoning server-side: after the
  // refresh it still renders collapsed by default and expands on demand.
  await expect(closedThinkingToggle).toHaveAttribute("aria-expanded", "false");
  await expect(thinkingText).toBeHidden();
  await page.screenshot({
    path: "test-results/thinking-committed-collapsed.png",
  });
  await closedThinkingToggle.click();
  await expect(thinkingText).toBeVisible();
  await expect(thinkingText).toContainText("prepare a workspace change");
  await page.screenshot({
    path: "test-results/thinking-committed-expanded.png",
  });

  const finalState = (await (
    await request.get(`${BACKEND_URL}/__test__/state`)
  ).json()) as { agent_output: string | null; command_marker: boolean };
  expect(finalState.agent_output).toBe("written by scripted model\n");
  expect(finalState.command_marker).toBe(true);
});
