import { afterEach, expect, test } from "vitest";

import { ApiError } from "../../api/client";
import { EvaluationClient } from "./evaluationApi";

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

test("requests the three read-only endpoints with encoded path segments", async () => {
  const paths: string[] = [];
  globalThis.fetch = async (input) => {
    paths.push(String(input));
    return jsonResponse([]);
  };
  const client = new EvaluationClient();

  await client.listCampaigns();
  await client.campaignDetail("judged campaign");
  await client.runDetail("judged campaign", "demo task", 2);

  expect(paths).toEqual([
    "/api/evaluations",
    "/api/evaluations/judged%20campaign",
    "/api/evaluations/judged%20campaign/runs/demo%20task/2",
  ]);
});

test("never attaches the CSRF token to read-only requests", async () => {
  const headers: Headers[] = [];
  globalThis.fetch = async (_input, init) => {
    headers.push(new Headers(init?.headers));
    return jsonResponse([]);
  };
  const client = new EvaluationClient();

  await client.listCampaigns();

  expect(headers[0].get("X-CSRF-Token")).toBeNull();
});

test("surfaces a failed request as an ApiError with its status", async () => {
  globalThis.fetch = async () =>
    jsonResponse({ detail: { code: "CAMPAIGN_NOT_FOUND" } }, 404);
  const client = new EvaluationClient();

  await expect(client.campaignDetail("missing")).rejects.toBeInstanceOf(
    ApiError,
  );
  await expect(client.campaignDetail("missing")).rejects.toThrow(
    "API request failed with status 404",
  );
});
