import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

const BACKEND_URL = "http://127.0.0.1:8000";

async function scriptedState(request: APIRequestContext) {
  const response = await request.get(`${BACKEND_URL}/__test__/state`);
  expect(response.ok()).toBe(true);
  return (await response.json()) as {
    workspace: string;
    generation: number;
    never_started_exists: boolean;
  };
}

async function createSession(page: Page, workspace: string, title: string) {
  await page.goto("/");
  await page.getByRole("textbox", { name: "工作区" }).fill(workspace);
  await page.getByLabel(/会话名称/).fill(title);
  await page.getByRole("button", { name: "打开工作区" }).click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
}

test("Stop during streaming returns to Send and preserves visible history", async ({
  page,
  request,
}) => {
  const state = await scriptedState(request);
  await createSession(page, state.workspace, "Stop flow");

  await page.getByRole("textbox", { name: "消息" }).fill("stop-flow");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/Streaming until stopped/)).toBeVisible();
  await page.getByRole("button", { name: "停止", exact: true }).click();

  await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
  await page.reload();
  await expect(page.getByText("stop-flow", { exact: true })).toBeVisible();
  await expect(page.getByText(/Streaming until stopped/)).toBeVisible();
  await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
});

test("browser disconnect does not cancel the backend run", async ({
  page,
  request,
}) => {
  const state = await scriptedState(request);
  await createSession(page, state.workspace, "Disconnect flow");

  await page.getByRole("textbox", { name: "消息" }).fill("disconnect-flow");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(
    page.getByText(/Working while the browser is connected/),
  ).toBeVisible();

  await page.goto("about:blank");
  await page.waitForTimeout(1_200);
  await page.goto("/");

  await expect(
    page.getByText(/Backend completed while the browser was away\./),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
});

test("server restart reboots authentication and gates an unknown tool effect", async ({
  page,
  request,
}) => {
  const initial = await scriptedState(request);
  await createSession(page, initial.workspace, "Restart flow");

  await page.getByRole("textbox", { name: "消息" }).fill("restart-flow");
  await page.getByRole("button", { name: "发送" }).click();
  const approval = page.getByRole("region", { name: "待审批" });
  await expect(
    approval.getByRole("heading", { name: "run_command" }),
  ).toBeVisible();
  await approval.getByRole("button", { name: "批准" }).click();
  await expect(page.getByLabel("run_command 运行中")).toBeVisible();

  const oldBootstrap = (await (
    await request.get(`${BACKEND_URL}/api/bootstrap`)
  ).json()) as { csrf_token: string };
  const restart = await request.post(`${BACKEND_URL}/__test__/restart`);
  expect(restart.ok()).toBe(true);

  await expect
    .poll(
      async () => {
        try {
          return (await scriptedState(request)).generation;
        } catch {
          return initial.generation;
        }
      },
      { timeout: 12_000 },
    )
    .toBe(initial.generation + 1);

  const newBootstrap = (await (
    await request.get(`${BACKEND_URL}/api/bootstrap`)
  ).json()) as { csrf_token: string };
  expect(newBootstrap.csrf_token).not.toBe(oldBootstrap.csrf_token);
  const staleMutation = await request.post(`${BACKEND_URL}/api/sessions`, {
    headers: {
      "X-CSRF-Token": oldBootstrap.csrf_token,
      Origin: BACKEND_URL,
    },
    data: { workspace: initial.workspace, title: "must-not-exist" },
  });
  expect(staleMutation.status()).toBe(403);

  await expect(page.getByText("上一轮运行因服务重启而中断。")).toBeVisible({
    timeout: 12_000,
  });
  await expect(page.getByLabel("run_command 未知")).toBeVisible();
  await expect(page.getByLabel("write_file 已跳过")).toBeVisible();
  expect((await scriptedState(request)).never_started_exists).toBe(false);

  await page.getByRole("button", { name: "我已检查工作区/进程" }).click();
  await expect(
    page.getByRole("button", { name: "我已检查工作区/进程" }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
  await page
    .getByRole("textbox", { name: "消息" })
    .fill("continue after recovery");
  await expect(page.getByRole("button", { name: "发送" })).toBeEnabled();
  await page.reload();
  await expect(page.getByText("restart-flow", { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "消息" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
  expect((await scriptedState(request)).never_started_exists).toBe(false);
});
