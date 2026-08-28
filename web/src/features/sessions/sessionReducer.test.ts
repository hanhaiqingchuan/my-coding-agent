import { expect, test } from "vitest";

import contract from "../../api/schema.fixture.json";
import { ApiClient } from "../../api/client";
import { SessionSocket } from "../../api/socket";
import {
  APPROVAL_DECISIONS,
  APPROVAL_STATUSES,
  REQUIRED_DTO_FIELDS,
  RUN_STATES,
  STOP_REASONS,
  TOOL_EXECUTION_STATES,
  type DurableEvent,
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
} as const;

function snapshot(snapshotSeq: number, state: RunState = activeRun.state): SessionSnapshotDto {
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
  const once = reduceServerMessage(initial, { type: "durable", event: eventWithSeq(4) });
  const twice = reduceServerMessage(once, { type: "durable", event: eventWithSeq(4) });

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

test("connection changes retain an active run until a server snapshot says otherwise", () => {
  const connected = {
    ...createInitialSessionViewState(),
    snapshot: snapshot(2),
    connection: "connected" as const,
  };

  const offline = sessionViewReducer(connected, { type: "connection.changed", connection: "offline" });

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

test("switching or reconnecting a session closes the old transport and subscribes again", async () => {
  const sockets: BrowserSocket[] = [];
  const api = {
    bootstrap: async () => ({ csrf_token: "token", websocket_url: "ws://local.test/api/ws" }),
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

test("an expired authentication token is bootstrapped at most once before resubscribing", async () => {
  const sockets: BrowserSocket[] = [];
  let bootstraps = 0;
  const api = {
    bootstrap: async () => {
      bootstraps += 1;
      return { csrf_token: `token-${bootstraps}`, websocket_url: "ws://local.test/api/ws" };
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

test("a non-user socket close reconnects once, bootstraps again, and resubscribes", async () => {
  const sockets: BrowserSocket[] = [];
  let bootstraps = 0;
  const connections: string[] = [];
  const api = {
    bootstrap: async () => {
      bootstraps += 1;
      return { csrf_token: `token-${bootstraps}`, websocket_url: "ws://local.test/api/ws" };
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
  await Promise.resolve();

  expect(sockets).toHaveLength(2);
  expect(bootstraps).toBe(2);
  sockets[1].open();
  expect(JSON.parse(sockets[1].sent[0])).toMatchObject({
    type: "session.subscribe",
    session_id: "session-a",
  });

  sockets[1].closeFromServer(1006);
  await Promise.resolve();
  expect(sockets).toHaveLength(2);
  expect(connections.at(-1)).toBe("offline");
});
