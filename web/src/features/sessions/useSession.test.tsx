import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { ApiClient } from "../../api/client";
import type { ServerMessage, SessionSnapshotDto } from "../../api/types";
import { useSession } from "./useSession";

class BrowserSocket {
  static instances: BrowserSocket[] = [];

  onopen: WebSocket["onopen"] = null;
  onmessage: WebSocket["onmessage"] = null;
  onclose: WebSocket["onclose"] = null;
  onerror: WebSocket["onerror"] = null;

  constructor() {
    BrowserSocket.instances.push(this);
  }

  send(): void {}

  close(): void {}

  open(): void {
    this.onopen?.call(this as unknown as WebSocket, new Event("open"));
  }

  message(message: ServerMessage): void {
    this.onmessage?.call(
      this as unknown as WebSocket,
      new MessageEvent("message", { data: JSON.stringify(message) }),
    );
  }
}

function snapshot(
  sequence: number,
  state: "model_streaming" | "completed",
): SessionSnapshotDto {
  return {
    session: {
      id: "session-1",
      title: "Demo",
      workspace_realpath: "/tmp/demo",
      requires_recovery_ack: false,
      created_at: "2026-08-28T00:00:00Z",
      updated_at: "2026-08-28T00:00:00Z",
    },
    active_run:
      state === "completed"
        ? null
        : {
            id: "run-1",
            session_id: "session-1",
            state,
            stop_reason: null,
            error_kind: null,
            cancellation_requested_at: null,
            config_snapshot: {},
            started_at: "2026-08-28T00:00:00Z",
            finished_at: null,
          },
    messages:
      state === "completed"
        ? [
            {
              id: "answer-1",
              session_id: "session-1",
              run_id: "run-1",
              seq: 1,
              role: "assistant",
              parts: [
                { type: "text", text: "Finished from the durable store." },
              ],
              status: "committed",
              tool_call_id: null,
            },
          ]
        : [],
    tools: [],
    pending_approval: null,
    interrupted_banner: null,
    snapshot_seq: sequence,
  };
}

afterEach(() => {
  BrowserSocket.instances = [];
  vi.unstubAllGlobals();
});

test("a durable event refreshes the authoritative session snapshot", async () => {
  vi.stubGlobal("WebSocket", BrowserSocket);
  const api = {
    bootstrap: async () => ({
      csrf_token: "token",
      websocket_url: "ws://local.test/api/ws",
    }),
    clearToken: () => undefined,
    snapshot: async () => snapshot(7, "completed"),
  } as unknown as ApiClient;
  const { result } = renderHook(() => useSession(api, "session-1"));

  await waitFor(() => expect(BrowserSocket.instances).toHaveLength(1));
  act(() => BrowserSocket.instances[0].open());
  act(() =>
    BrowserSocket.instances[0].message({
      type: "snapshot",
      client_command_id: "subscribe-1",
      session_id: "session-1",
      snapshot: snapshot(2, "model_streaming"),
    }),
  );
  expect(result.current.state.snapshot?.active_run?.state).toBe(
    "model_streaming",
  );

  act(() =>
    BrowserSocket.instances[0].message({
      type: "durable",
      event: {
        seq: 7,
        session_id: "session-1",
        run_id: "run-1",
        type: "run.finished",
        payload: { state: "completed" },
        created_at: "2026-08-28T00:00:01Z",
      },
    }),
  );

  await waitFor(() =>
    expect(result.current.state.snapshot?.active_run).toBeNull(),
  );
  expect(result.current.state.snapshot?.messages[0].parts[0]).toEqual({
    type: "text",
    text: "Finished from the durable store.",
  });
});
