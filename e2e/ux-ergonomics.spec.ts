import { expect, test, type Page, type APIRequestContext } from "@playwright/test";

const BACKEND_URL = "http://127.0.0.1:8000";

/** Open a fresh session named for this test, mirroring the agent-flow bootstrap. */
async function createSession(
  page: Page,
  request: APIRequestContext,
  name: string,
): Promise<string> {
  const stateResponse = await request.get(`${BACKEND_URL}/__test__/state`);
  expect(stateResponse.ok()).toBe(true);
  const state = (await stateResponse.json()) as { workspace: string };
  const title = `${name} ${Date.now()}`;

  await page.goto("/");
  await page.getByRole("button", { name: "创建新会话" }).click();
  await page.getByRole("textbox", { name: "工作区" }).fill(state.workspace);
  await page.getByLabel(/会话名称/).fill(title);
  await page.getByRole("button", { name: "打开工作区" }).click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
  return title;
}

test("renders assistant markdown with the context gauge and context-load list", async ({
  page,
  request,
}) => {
  await createSession(page, request, "Markdown gauge");

  await page.getByRole("textbox", { name: "消息" }).fill("markdown-flow");
  await page.getByRole("button", { name: "发送" }).click();

  // The committed assistant message renders through the Markdown subset:
  // bold, a list, and a fenced code block with its language and copy button.
  await expect(page.locator(".message-assistant strong")).toHaveText("summary");
  await expect(page.locator(".message-assistant li")).toHaveCount(2);
  const codeBlock = page.locator(".md-code-block");
  await expect(codeBlock.locator(".md-code-lang")).toHaveText("python");
  await expect(codeBlock.locator("code")).toHaveText("print('hello')");
  await expect(codeBlock.getByRole("button", { name: "复制" })).toBeVisible();

  // The gauge under the composer reports the finished run's recorded estimate.
  const gauge = page.getByRole("status", { name: "上下文占用" });
  await expect(gauge).toBeVisible();
  await expect(gauge).toContainText(/\d+%/);

  // The right rail lists what the run loaded: the temp workspace has no AGENTS.md
  // and the model pulled no skill, and both absences are stated, not hidden.
  await expect(page.getByText("本工作区没有 AGENTS.md")).toBeVisible();
  await expect(page.getByText("本次运行未读取技能。")).toBeVisible();
});

test("the approval toggle skips the docks, completes the run, and survives reload", async ({
  page,
  request,
}) => {
  await createSession(page, request, "Auto approve");
  // The button's label names the mode a click switches TO: 人工审批 is shown
  // while the session is in auto-approve mode.
  const toggle = page.getByRole("button", { name: "自动批准" });
  await expect(toggle).toHaveAttribute("aria-pressed", "false");

  await toggle.click();
  await expect(
    page.getByRole("button", { name: "人工审批" }),
  ).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("textbox", { name: "消息" }).fill("agent-flow");
  await page.getByRole("button", { name: "发送" }).click();
  // The scripted round-one thinking block parks open until released.
  await request.post(`${BACKEND_URL}/__test__/thinking/release`);

  // Auto-approve runs both gated tools end to end: no dock ever appears.
  // Scope every wait to this session's timeline: restored sessions from the
  // shared scripted database carry the same text and tool cards.
  const timeline = page.getByRole("region", { name: "对话时间线" });
  await expect(
    page.getByRole("region", { name: "待审批" }),
  ).toHaveCount(0);
  await expect(
    timeline.getByText("All scripted steps completed."),
  ).toBeVisible();
  await expect(timeline.getByLabel("write_file 成功")).toBeVisible();
  await expect(timeline.getByLabel("run_command 成功")).toBeVisible();

  // The mode is a durable session field: it survives a reload.
  await page.reload();
  await expect(
    page.getByRole("button", { name: "人工审批" }),
  ).toHaveAttribute("aria-pressed", "true");
});

test("both rails collapse to strips and restore their content", async ({
  page,
  request,
}) => {
  await createSession(page, request, "Rails");

  const details = page.getByRole("complementary", { name: "运行详情" });
  await expect(details.getByText("当前没有运行中的任务。")).toBeVisible();
  await details.getByRole("button", { name: "»" }).click();
  await expect(details).toHaveAttribute("data-collapsed", "true");
  await expect(
    details.getByText("当前没有运行中的任务。"),
  ).toHaveCount(0);
  await details.getByRole("button", { name: "«" }).click();
  await expect(details.getByText("当前没有运行中的任务。")).toBeVisible();

  const sidebar = page.getByRole("navigation", { name: "会话与工作区" });
  await sidebar.getByRole("button", { name: "«" }).click();
  await expect(sidebar).toHaveAttribute("data-collapsed", "true");
  // The conversation stays reachable while the session list is folded away.
  await expect(page.getByRole("main", { name: "对话" })).toBeVisible();
});

test("/compact force-compacts the transcript and surfaces the compaction chip", async ({
  page,
  request,
}) => {
  await createSession(page, request, "Manual compact");

  // Three completed turns leave the first assistant group outside the recent
  // window, so the forced compaction has a replaceable group to summarize.
  // Wait on THIS session's own timeline growing: count the turn's user bubble
  // and its reply. Text-based waits are unreliable here — the same scripted
  // reply exists in other sessions restored from the shared test database,
  // and the Send button briefly reads 发送 again during snapshot refreshes
  // while the run is still settling (clicking through issues run.stop).
  for (let index = 1; index <= 3; index += 1) {
    await page.getByRole("textbox", { name: "消息" }).fill(`turn ${index}`);
    await page.getByRole("button", { name: "发送" }).click();
    const timeline = page.getByRole("region", { name: "对话时间线" });
    await expect(
      timeline.getByText(`turn ${index}`, { exact: true }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(timeline.getByText("Scripted request completed.")).toHaveCount(
      index,
      { timeout: 15_000 },
    );
  }

  await page.getByRole("textbox", { name: "消息" }).fill("/compact");
  await page.getByRole("button", { name: "发送" }).click();

  await expect(
    page.getByText(/上下文已压缩：[\d,]+ → [\d,]+ tokens/),
  ).toBeVisible({ timeout: 15_000 });
});
