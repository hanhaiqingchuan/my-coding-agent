import { expect, test } from "@playwright/test";

const BACKEND_URL = "http://127.0.0.1:8000";

test("creates a session, approves write and command, then restores final history", async ({
  page,
  request,
}) => {
  const stateResponse = await request.get(`${BACKEND_URL}/__test__/state`);
  expect(stateResponse.ok()).toBe(true);
  const state = (await stateResponse.json()) as { workspace: string };

  await page.goto("/");
  await page.getByRole("textbox", { name: "Workspace" }).fill(state.workspace);
  await page.getByLabel(/Session title/).fill("Complete flow");
  await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(
    page.getByRole("heading", { name: "Complete flow" }),
  ).toBeVisible();

  await page.getByRole("textbox", { name: "Message" }).fill("agent-flow");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Preparing the workspace change…")).toBeVisible();

  const writeApproval = page.getByRole("region", { name: "Pending approval" });
  await expect(
    writeApproval.getByRole("heading", { name: "write_file" }),
  ).toBeVisible();
  await writeApproval.getByRole("button", { name: "Approve" }).click();

  await expect(
    writeApproval.getByRole("heading", { name: "run_command" }),
  ).toBeVisible();
  await expect(
    writeApproval.getByText(
      "test -f agent-output.txt && printf verified > command-marker.txt",
    ),
  ).toBeVisible();
  await expect(writeApproval.getByText("10s")).toBeVisible();
  await expect(
    writeApproval.getByText(/This command is not sandboxed/),
  ).toBeVisible();
  await writeApproval.getByRole("button", { name: "Approve" }).click();

  await expect(page.getByText("All scripted steps completed.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
  await expect(page.getByLabel("write_file succeeded")).toBeVisible();
  await expect(page.getByLabel("run_command succeeded")).toBeVisible();

  await page.reload();
  await expect(page.getByText("All scripted steps completed.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();

  const finalState = (await (
    await request.get(`${BACKEND_URL}/__test__/state`)
  ).json()) as { agent_output: string | null; command_marker: boolean };
  expect(finalState.agent_output).toBe("written by scripted model\n");
  expect(finalState.command_marker).toBe(true);
});
