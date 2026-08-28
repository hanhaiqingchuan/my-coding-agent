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
            totals: {
              input_tokens: 0,
              output_tokens: 0,
              cache_creation_input_tokens: 0,
              cache_read_input_tokens: 0,
              round_count: 1,
              retry_count: 0,
            },
          },
    last_finished_run: null,
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

// Longer than the whole bounded refresh backoff, so nothing stays pending.
const RETRY_WINDOW_MS = 5_000;
// The first fetch plus every bounded retry of useSession's refresh loop.
const MAX_SNAPSHOT_FETCHES = 4;

function finishedEvent(sequence: number): ServerMessage {
  return {
    type: "durable",
    event: {
      seq: sequence,
      session_id: "session-1",
      run_id: "run-1",
      type: "run.finished",
      payload: { state: "completed" },
      created_at: "2026-08-28T00:00:01Z",
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

async function settlePendingWork(elapsedMs = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(elapsedMs);
  });
}

afterEach(() => {
  BrowserSocket.instances = [];
  vi.unstubAllGlobals();
  vi.useRealTimers();
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

test("a transiently failing refresh retries so the last durable update still lands", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", BrowserSocket);
  let snapshotCalls = 0;
  const api = {
    bootstrap: async () => ({
      csrf_token: "token",
      websocket_url: "ws://local.test/api/ws",
    }),
    clearToken: () => undefined,
    snapshot: async () => {
      snapshotCalls += 1;
      if (snapshotCalls === 1) throw new TypeError("the backend is restarting");
      return snapshot(7, "completed");
    },
  } as unknown as ApiClient;
  const { result } = renderHook(() => useSession(api, "session-1"));

  await settlePendingWork();
  expect(BrowserSocket.instances).toHaveLength(1);
  act(() => BrowserSocket.instances[0].open());
  act(() =>
    BrowserSocket.instances[0].message({
      type: "snapshot",
      client_command_id: "subscribe-1",
      session_id: "session-1",
      snapshot: snapshot(2, "model_streaming"),
    }),
  );
  act(() => BrowserSocket.instances[0].message(finishedEvent(7)));
  await settlePendingWork();

  expect(snapshotCalls).toBe(1);
  expect(result.current.state.snapshot?.active_run?.state).toBe(
    "model_streaming",
  );

  await settlePendingWork(RETRY_WINDOW_MS);

  expect(snapshotCalls).toBe(2);
  expect(result.current.state.snapshot?.active_run).toBeNull();
});

test("a permanently failing refresh stops after its bounded retries", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", BrowserSocket);
  let snapshotCalls = 0;
  const api = {
    bootstrap: async () => ({
      csrf_token: "token",
      websocket_url: "ws://local.test/api/ws",
    }),
    clearToken: () => undefined,
    snapshot: async () => {
      snapshotCalls += 1;
      throw new TypeError("the backend is unreachable");
    },
  } as unknown as ApiClient;
  renderHook(() => useSession(api, "session-1"));

  await settlePendingWork();
  act(() => BrowserSocket.instances[0].open());
  act(() => BrowserSocket.instances[0].message(finishedEvent(7)));
  await settlePendingWork();
  expect(snapshotCalls).toBe(1);

  await settlePendingWork(RETRY_WINDOW_MS);

  expect(snapshotCalls).toBe(MAX_SNAPSHOT_FETCHES);
  expect(vi.getTimerCount()).toBe(0);
});

test("unmounting during a refresh backoff cancels the pending retry", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", BrowserSocket);
  let snapshotCalls = 0;
  const api = {
    bootstrap: async () => ({
      csrf_token: "token",
      websocket_url: "ws://local.test/api/ws",
    }),
    clearToken: () => undefined,
    snapshot: async () => {
      snapshotCalls += 1;
      throw new TypeError("the backend is unreachable");
    },
  } as unknown as ApiClient;
  const { unmount } = renderHook(() => useSession(api, "session-1"));

  await settlePendingWork();
  act(() => BrowserSocket.instances[0].open());
  act(() => BrowserSocket.instances[0].message(finishedEvent(7)));
  await settlePendingWork();
  expect(snapshotCalls).toBe(1);

  act(() => unmount());
  await settlePendingWork(RETRY_WINDOW_MS);

  expect(snapshotCalls).toBe(1);
  expect(vi.getTimerCount()).toBe(0);
});

test("durable events during an in-flight refresh coalesce into one more fetch", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", BrowserSocket);
  const inFlight = deferred<SessionSnapshotDto>();
  let snapshotCalls = 0;
  const api = {
    bootstrap: async () => ({
      csrf_token: "token",
      websocket_url: "ws://local.test/api/ws",
    }),
    clearToken: () => undefined,
    snapshot: () => {
      snapshotCalls += 1;
      if (snapshotCalls === 1) return inFlight.promise;
      return Promise.resolve(snapshot(9, "completed"));
    },
  } as unknown as ApiClient;
  const { result } = renderHook(() => useSession(api, "session-1"));

  await settlePendingWork();
  act(() => BrowserSocket.instances[0].open());
  act(() =>
    BrowserSocket.instances[0].message({
      type: "snapshot",
      client_command_id: "subscribe-1",
      session_id: "session-1",
      snapshot: snapshot(2, "model_streaming"),
    }),
  );
  act(() => BrowserSocket.instances[0].message(finishedEvent(5)));
  await settlePendingWork();
  act(() => BrowserSocket.instances[0].message(finishedEvent(6)));
  act(() => BrowserSocket.instances[0].message(finishedEvent(7)));
  act(() => BrowserSocket.instances[0].message(finishedEvent(8)));
  await settlePendingWork();
  expect(snapshotCalls).toBe(1);

  // The in-flight response was cut before those events, so it is already stale.
  inFlight.resolve(snapshot(5, "model_streaming"));
  await settlePendingWork(RETRY_WINDOW_MS);

  expect(snapshotCalls).toBe(2);
  expect(result.current.state.snapshot?.active_run).toBeNull();
  expect(result.current.state.snapshot?.snapshot_seq).toBe(9);
});
