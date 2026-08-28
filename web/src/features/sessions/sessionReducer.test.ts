import { afterEach, expect, test, vi } from "vitest";

import contract from "../../api/schema.fixture.json";
import { ApiClient, ApiError } from "../../api/client";
import { SessionSocket } from "../../api/socket";
import {
  APPROVAL_DECISIONS,
  APPROVAL_STATUSES,
  REQUIRED_DTO_FIELDS,
  RUN_STATES,
  STOP_REASONS,
  TOOL_EXECUTION_STATES,
  type DurableEvent,
  type BootstrapDto,
  type RunState,
  type SessionSnapshotDto,
} from "../../api/types";
import {
  createInitialSessionViewState,
  reduceServerMessage,
  sessionViewReducer,
} from "./sessionReducer";

const activeRun = {
  id: "run-1",
  session_id: "session-1",
  state: "model_streaming",
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
    round_count: 0,
    retry_count: 0,
  },
} as const;

function snapshot(
  snapshotSeq: number,
  state: RunState = activeRun.state,
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
    active_run: { ...activeRun, state },
    last_finished_run: null,
    messages: [],
    tools: [],
    pending_approval: null,
    interrupted_banner: null,
    snapshot_seq: snapshotSeq,
  };
}

function eventWithSeq(seq: number): DurableEvent {
  return {
    seq,
    session_id: "session-1",
    run_id: "run-1",
    type: "run.state_changed",
    payload: { state: "cancelling" },
    created_at: "2026-08-28T00:00:00Z",
  };
}

test("frontend protocol constants match the fixed backend schema fixture", () => {
  expect(RUN_STATES).toEqual(contract.enums.RunState);
  expect(STOP_REASONS).toEqual(contract.enums.StopReason);
  expect(APPROVAL_STATUSES).toEqual(contract.enums.ApprovalStatus);
  expect(APPROVAL_DECISIONS).toEqual(contract.enums.ApprovalDecision);
  expect(TOOL_EXECUTION_STATES).toEqual(contract.enums.ToolExecutionState);
  expect(REQUIRED_DTO_FIELDS).toEqual(contract.required);
});

test("a snapshot replaces locally inferred state and resets the durable cursor", () => {
  const inferred = {
    ...createInitialSessionViewState(),
    snapshot: snapshot(19),
    lastSeq: 19,
  };

  const next = reduceServerMessage(inferred, {
    type: "snapshot",
    client_command_id: "subscribe-1",
    session_id: "session-1",
    snapshot: snapshot(4, "cancelling"),
  });

  expect(next.snapshot?.active_run?.state).toBe("cancelling");
  expect(next.lastSeq).toBe(4);
});

test("ignores an already-applied durable event", () => {
  const initial = { ...createInitialSessionViewState(), lastSeq: 3 };
  const once = reduceServerMessage(initial, {
    type: "durable",
    event: eventWithSeq(4),
  });
  const twice = reduceServerMessage(once, {
    type: "durable",
    event: eventWithSeq(4),
  });

  expect(twice).toEqual(once);
});

test("a transient assistant delta leaves the durable cursor unchanged", () => {
  const initial = { ...createInitialSessionViewState(), lastSeq: 7 };

  const next = reduceServerMessage(initial, {
    type: "assistant.delta",
    session_id: "session-1",
    run_id: "run-1",
    draft_epoch: "attempt-1",
    index: 0,
    text: "Working…",
  });

  expect(next.lastSeq).toBe(7);
  expect(next.assistantDrafts["attempt-1"]).toBe("Working…");
});

test("a durable refresh preserves a live draft until its message is committed", () => {
  const streaming = {
    ...createInitialSessionViewState(),
    snapshot: snapshot(2),
    lastSeq: 2,
    assistantDrafts: { "attempt-1": "Working…" },
  };

  const refreshed = sessionViewReducer(streaming, {
    type: "snapshot.refreshed",
    snapshot: snapshot(3, "awaiting_approval"),
  });

  expect(refreshed.assistantDrafts).toEqual({ "attempt-1": "Working…" });
  expect(refreshed.snapshot?.active_run?.state).toBe("awaiting_approval");
});

test("a durable refresh rejects the whole snapshot when its sequence is stale", () => {
  const current = {
    ...createInitialSessionViewState(),
    snapshot: snapshot(8, "model_streaming"),
    lastSeq: 9,
    assistantDrafts: { "attempt-1": "newer draft" },
  };

  const refreshed = sessionViewReducer(current, {
    type: "snapshot.refreshed",
    snapshot: snapshot(7, "completed"),
  });

  expect(refreshed).toBe(current);
});

test("a durable refresh at the current sequence is accepted idempotently", () => {
  const current = {
    ...createInitialSessionViewState(),
    snapshot: snapshot(6, "model_streaming"),
    lastSeq: 7,
    assistantDrafts: { "attempt-1": "finished draft" },
  };
  const terminal = snapshot(7, "completed");

  const once = sessionViewReducer(current, {
    type: "snapshot.refreshed",
    snapshot: terminal,
  });
  const twice = sessionViewReducer(once, {
    type: "snapshot.refreshed",
    snapshot: terminal,
  });

  expect(once.snapshot).toBe(terminal);
  expect(once.lastSeq).toBe(7);
  expect(once.assistantDrafts).toEqual({});
  expect(twice).toEqual(once);
});

test("a terminal durable event clears transient assistant and tool output drafts", () => {
  const streaming = {
    ...createInitialSessionViewState(),
    lastSeq: 7,
    assistantDrafts: { "attempt-1": "Incomplete answer" },
    toolOutputDrafts: { "call-1": "partial output" },
  };

  const next = reduceServerMessage(streaming, {
    type: "durable",
    event: {
      ...eventWithSeq(8),
      type: "run.finished",
      payload: { state: "completed" },
    },
  });

  expect(next.assistantDrafts).toEqual({});
  expect(next.toolOutputDrafts).toEqual({});
});

test("connection changes retain an active run until a server snapshot says otherwise", () => {
  const connected = {
    ...createInitialSessionViewState(),
    snapshot: snapshot(2),
    connection: "connected" as const,
  };

  const offline = sessionViewReducer(connected, {
    type: "connection.changed",
    connection: "offline",
  });

  expect(offline.connection).toBe("offline");
  expect(offline.snapshot?.active_run?.id).toBe("run-1");
});

class BrowserSocket {
  onopen: WebSocket["onopen"] = null;
  onmessage: WebSocket["onmessage"] = null;
  onclose: WebSocket["onclose"] = null;
  onerror: WebSocket["onerror"] = null;
  readonly sent: string[] = [];
  closed = false;

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
  }

  open(): void {
    this.onopen?.call(this as unknown as WebSocket, new Event("open"));
  }

  closeFromServer(code: number): void {
    this.onclose?.call(this as unknown as WebSocket, { code } as CloseEvent);
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  vi.useRealTimers();
});

test("switching or reconnecting a session closes the old transport and subscribes again", async () => {
  const sockets: BrowserSocket[] = [];
  const api = {
    bootstrap: async () => ({
      csrf_token: "token",
      websocket_url: "ws://local.test/api/ws",
    }),
    clearToken: () => undefined,
  } as unknown as ApiClient;
  const socket = new SessionSocket({
    api,
    onMessage: () => undefined,
    onConnection: () => undefined,
    onToken: () => undefined,
    createSocket: () => {
      const connection = new BrowserSocket();
      sockets.push(connection);
      return connection;
    },
  });

  await socket.connect("session-a");
  sockets[0].open();
  await socket.connect("session-b");
  sockets[1].open();

  expect(sockets[0].closed).toBe(true);
  expect(JSON.parse(sockets[1].sent[0])).toMatchObject({
    type: "session.subscribe",
    session_id: "session-b",
    payload: {},
  });
});

test("a stale bootstrap 403 after switching sessions cannot clear the new session token", async () => {
  const oldBootstrap = deferred<BootstrapDto>();
  const sockets: BrowserSocket[] = [];
  const tokens: Array<string | null> = [];
  let bootstrapCalls = 0;
  let clearCalls = 0;
  const api = {
    bootstrap: () => {
      bootstrapCalls += 1;
      if (bootstrapCalls === 1) return oldBootstrap.promise;
      return Promise.resolve({
        csrf_token: "new-token",
        websocket_url: "ws://local.test/api/ws",
      });
    },
    clearToken: () => {
      clearCalls += 1;
    },
  } as unknown as ApiClient;
  const socket = new SessionSocket({
    api,
    onMessage: () => undefined,
    onConnection: () => undefined,
    onToken: (token) => tokens.push(token),
    createSocket: () => {
      const connection = new BrowserSocket();
      sockets.push(connection);
      return connection;
    },
  });

  const oldConnection = socket.connect("session-old");
  await Promise.resolve();
  await socket.connect("session-new");
  oldBootstrap.reject(new ApiError(403, "expired"));
  await oldConnection;
  await Promise.resolve();

  expect(clearCalls).toBe(0);
  expect(bootstrapCalls).toBe(2);
  expect(sockets).toHaveLength(1);
  expect(tokens).toEqual(["new-token"]);
});

test("a stale bootstrap 403 after explicit close leaves shared token state untouched", async () => {
  const oldBootstrap = deferred<BootstrapDto>();
  const tokens: Array<string | null> = [];
  let bootstrapCalls = 0;
  let clearCalls = 0;
  const api = {
    bootstrap: () => {
      bootstrapCalls += 1;
      return oldBootstrap.promise;
    },
    clearToken: () => {
      clearCalls += 1;
    },
  } as unknown as ApiClient;
  const socket = new SessionSocket({
    api,
    onMessage: () => undefined,
    onConnection: () => undefined,
    onToken: (token) => tokens.push(token),
    createSocket: () => new BrowserSocket(),
  });

  const oldConnection = socket.connect("session-old");
  await Promise.resolve();
  socket.close();
  oldBootstrap.reject(new ApiError(403, "expired"));
  await oldConnection;
  await Promise.resolve();

  expect(clearCalls).toBe(0);
  expect(bootstrapCalls).toBe(1);
  expect(tokens).toEqual([]);
});

test("an expired authentication token is bootstrapped at most once before resubscribing", async () => {
  const sockets: BrowserSocket[] = [];
  let bootstraps = 0;
  const api = {
    bootstrap: async () => {
      bootstraps += 1;
      return {
        csrf_token: `token-${bootstraps}`,
        websocket_url: "ws://local.test/api/ws",
      };
    },
    clearToken: () => undefined,
  } as unknown as ApiClient;
  const socket = new SessionSocket({
    api,
    onMessage: () => undefined,
    onConnection: () => undefined,
    onToken: () => undefined,
    createSocket: () => {
      const connection = new BrowserSocket();
      sockets.push(connection);
      return connection;
    },
  });

  await socket.connect("session-a");
  sockets[0].open();
  sockets[0].closeFromServer(4401);
  await Promise.resolve();
  expect(sockets).toHaveLength(2);
  sockets[1].open();
  sockets[1].closeFromServer(4401);
  await Promise.resolve();

  expect(bootstraps).toBe(2);
  expect(JSON.parse(sockets[1].sent[0])).toMatchObject({
    type: "session.subscribe",
    session_id: "session-a",
  });
});

test("each non-user socket close bootstraps again and resubscribes", async () => {
  vi.useFakeTimers();
  const sockets: BrowserSocket[] = [];
  let bootstraps = 0;
  const connections: string[] = [];
  const api = {
    bootstrap: async () => {
      bootstraps += 1;
      return {
        csrf_token: `token-${bootstraps}`,
        websocket_url: "ws://local.test/api/ws",
      };
    },
    clearToken: () => undefined,
  } as unknown as ApiClient;
  const socket = new SessionSocket({
    api,
    onMessage: () => undefined,
    onConnection: (connection) => connections.push(connection),
    onToken: () => undefined,
    createSocket: () => {
      const connection = new BrowserSocket();
      sockets.push(connection);
      return connection;
    },
  });

  await socket.connect("session-a");
  sockets[0].open();
  sockets[0].closeFromServer(4408);
  await vi.advanceTimersByTimeAsync(250);

  expect(sockets).toHaveLength(2);
  expect(bootstraps).toBe(2);
  sockets[1].open();
  expect(JSON.parse(sockets[1].sent[0])).toMatchObject({
    type: "session.subscribe",
    session_id: "session-a",
  });

  sockets[1].closeFromServer(1006);
  await vi.advanceTimersByTimeAsync(500);
  expect(sockets).toHaveLength(3);
  expect(connections.at(-1)).toBe("reconnecting");
  sockets[2].open();
  expect(connections.at(-1)).toBe("connected");
  socket.close();
});

test("a server that closes right after accepting is retried on the bounded backoff", async () => {
  vi.useFakeTimers();
  const sockets: BrowserSocket[] = [];
  let bootstraps = 0;
  const api = {
    bootstrap: async () => {
      bootstraps += 1;
      return {
        csrf_token: `token-${bootstraps}`,
        websocket_url: "ws://local.test/api/ws",
      };
    },
    clearToken: () => undefined,
  } as unknown as ApiClient;
  const socket = new SessionSocket({
    api,
    onMessage: () => undefined,
    onConnection: () => undefined,
    onToken: () => undefined,
    createSocket: () => {
      const connection = new BrowserSocket();
      sockets.push(connection);
      return connection;
    },
  });

  await socket.connect("session-a");
  sockets[0].open();
  sockets[0].closeFromServer(1006);
  await Promise.resolve();

  // No reconnect inside the same tick, so an accept-then-close server cannot spin
  // the bootstrap endpoint.
  expect(bootstraps).toBe(1);
  expect(vi.getTimerCount()).toBe(1);
  await vi.advanceTimersByTimeAsync(250);
  expect(bootstraps).toBe(2);
  expect(sockets).toHaveLength(2);

  // The next wait is longer than the first one.
  sockets[1].open();
  sockets[1].closeFromServer(1006);
  await vi.advanceTimersByTimeAsync(250);
  expect(bootstraps).toBe(2);
  await vi.advanceTimersByTimeAsync(250);
  expect(bootstraps).toBe(3);
  expect(sockets).toHaveLength(3);

  // Teardown leaves no timer behind.
  sockets[2].open();
  sockets[2].closeFromServer(1006);
  await Promise.resolve();
  expect(vi.getTimerCount()).toBe(1);
  socket.close();
  expect(vi.getTimerCount()).toBe(0);
  await vi.advanceTimersByTimeAsync(2_000);
  expect(bootstraps).toBe(3);
});

test("a temporary bootstrap failure during restart keeps retrying until the server returns", async () => {
  vi.useFakeTimers();
  const sockets: BrowserSocket[] = [];
  const connections: string[] = [];
  let bootstraps = 0;
  const api = {
    bootstrap: async () => {
      bootstraps += 1;
      if (bootstraps === 2) throw new TypeError("backend is restarting");
      return {
        csrf_token: `token-${bootstraps}`,
        websocket_url: "ws://local.test/api/ws",
      };
    },
    clearToken: () => undefined,
  } as unknown as ApiClient;
  const socket = new SessionSocket({
    api,
    onMessage: () => undefined,
    onConnection: (connection) => connections.push(connection),
    onToken: () => undefined,
    createSocket: () => {
      const connection = new BrowserSocket();
      sockets.push(connection);
      return connection;
    },
  });

  await socket.connect("session-a");
  sockets[0].open();
  sockets[0].closeFromServer(1006);
  await Promise.resolve();
  expect(bootstraps).toBe(1);

  // The first backoff reconnects; that bootstrap is the one the restart fails.
  await vi.runOnlyPendingTimersAsync();
  expect(bootstraps).toBe(2);

  await vi.runOnlyPendingTimersAsync();
  expect(bootstraps).toBe(3);
  expect(sockets).toHaveLength(2);
  sockets[1].open();

  expect(connections.at(-1)).toBe("connected");
  expect(JSON.parse(sockets[1].sent[0])).toMatchObject({
    type: "session.subscribe",
    session_id: "session-a",
  });
  socket.close();
});
