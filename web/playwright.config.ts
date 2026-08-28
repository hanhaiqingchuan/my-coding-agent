import { defineConfig } from "@playwright/test";

const backendEnvironment = Object.fromEntries(
  Object.entries(process.env).filter(
    ([name, value]) =>
      value !== undefined &&
      ![
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "GOOGLE_API_KEY",
      ].includes(name),
  ),
) as Record<string, string>;

export default defineConfig({
  testDir: "../e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "uv run --python 3.12 tests/support/scripted_server.py",
      cwd: "..",
      env: backendEnvironment,
      url: "http://127.0.0.1:8000/api/health",
      reuseExistingServer: false,
      timeout: 30_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort",
      cwd: ".",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: false,
      timeout: 30_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    },
  ],
});
