import { chromium } from "@playwright/test";
const BASE = "http://127.0.0.1:5173";
const API = "http://127.0.0.1:8000";
const state = await fetch(`${API}/__test__/state`).then((r) => r.json());
const bootstrap = await fetch(`${API}/api/bootstrap`).then((r) => r.json());
await fetch(`${API}/api/sessions`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-CSRF-Token": bootstrap.csrf_token,
  },
  body: JSON.stringify({ workspace: state.workspace, title: "品牌页" }),
});
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(`${BASE}/#/`);
await page
  .getByRole("button", { name: /品牌页/ })
  .first()
  .click();
await page.waitForTimeout(500);
await page.screenshot({ path: "/tmp/agent-shots/32-brand-empty.png" });

// the dialog with the visual browser open
await page.getByRole("button", { name: "创建新会话" }).click();
await page.getByRole("button", { name: "浏览…" }).click();
await page.waitForTimeout(800);
await page.screenshot({ path: "/tmp/agent-shots/33-new-session-dialog.png" });
await page.getByRole("button", { name: "取消" }).click();

// a run for the new labels
await page.getByRole("textbox", { name: "消息" }).fill("agent-flow");
await page.getByRole("button", { name: "发送" }).click();
await fetch(`${API}/__test__/thinking/release`, { method: "POST" });
const dock = page.getByRole("region", { name: "待审批" });
await dock.getByRole("heading", { name: "write_file" }).waitFor();
await dock.getByRole("button", { name: "批准" }).click();
await dock.getByRole("heading", { name: "run_command" }).waitFor();
await dock.getByRole("button", { name: "批准" }).click();
await page.getByText("All scripted steps completed.").waitFor();
await page.waitForTimeout(500);
await page.screenshot({ path: "/tmp/agent-shots/34-renamed-labels.png" });

// evaluation charts with trend lines
await page.goto(`${BASE}/#/evaluations`);
await page.waitForTimeout(900);
await page.screenshot({ path: "/tmp/agent-shots/35-eval-trend.png" });
await browser.close();
console.log("done");
