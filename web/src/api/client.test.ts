import { afterEach, expect, test } from "vitest";

import { ApiClient } from "./client";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function jsonResponse(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("a successful bootstrap always replaces the in-memory token", async () => {
  let bootstrapCount = 0;
  globalThis.fetch = async () => {
    bootstrapCount += 1;
    return jsonResponse({
      csrf_token: bootstrapCount === 1 ? "old-token" : "new-token",
      websocket_url: "ws://local.test/api/ws",
    });
  };
  const client = new ApiClient();

  await client.bootstrap();
  await client.bootstrap();

  expect(client.getToken()).toBe("new-token");
});

test("a state-changing 403 clears the old token then bootstraps and retries once", async () => {
  const postTokens: Array<string | null> = [];
  let requestCount = 0;
  globalThis.fetch = async (_input, init) => {
    requestCount += 1;
    if (requestCount === 1) {
      return jsonResponse({
        csrf_token: "old-token",
        websocket_url: "ws://local.test/api/ws",
      });
    }
    if (requestCount === 2) {
      postTokens.push(new Headers(init?.headers).get("X-CSRF-Token"));
      return jsonResponse({}, 403);
    }
    if (requestCount === 3) {
      return jsonResponse({
        csrf_token: "fresh-token",
        websocket_url: "ws://local.test/api/ws",
      });
    }
    postTokens.push(new Headers(init?.headers).get("X-CSRF-Token"));
    return jsonResponse({
      id: "session-1",
      title: "Demo",
      workspace_realpath: "/tmp/demo",
      requires_recovery_ack: false,
      created_at: "2026-08-28T00:00:00Z",
      updated_at: "2026-08-28T00:00:00Z",
    });
  };
  const client = new ApiClient();

  const created = await client.createSession("/tmp/demo", "Demo");

  expect(created.id).toBe("session-1");
  expect(postTokens).toEqual(["old-token", "fresh-token"]);
  expect(requestCount).toBe(4);
  expect(client.getToken()).toBe("fresh-token");
});
