import type { ServerMessage, SessionSnapshotDto } from "../../api/types";

const TERMINAL_RUN_STATES = new Set(["completed", "stopped", "cancelled", "failed", "interrupted"]);

export type ConnectionState = "connecting" | "connected" | "reconnecting" | "offline";

export type SessionViewState = {
  snapshot: SessionSnapshotDto | null;
  draftText: string;
  connection: ConnectionState;
  lastSeq: number;
  csrfToken: string | null;
  assistantDrafts: Record<string, string>;
  toolOutputDrafts: Record<string, string>;
};

export type SessionViewAction =
  | { type: "server.message"; message: ServerMessage }
  | { type: "connection.changed"; connection: ConnectionState }
  | { type: "csrf.changed"; csrfToken: string | null }
  | { type: "draft.changed"; draftText: string }
  | { type: "session.selected" };

export function createInitialSessionViewState(): SessionViewState {
  return {
    snapshot: null,
    draftText: "",
    connection: "offline",
    lastSeq: 0,
    csrfToken: null,
    assistantDrafts: {},
    toolOutputDrafts: {},
  };
}

export function reduceServerMessage(
  state: SessionViewState,
  message: ServerMessage,
): SessionViewState {
  if (message.type === "snapshot") {
    return {
      ...state,
      snapshot: message.snapshot,
      lastSeq: message.snapshot.snapshot_seq,
      assistantDrafts: {},
      toolOutputDrafts: {},
    };
  }

  if (message.type === "durable") {
    if (message.event.seq <= state.lastSeq) {
      return state;
    }
    const eventState = message.event.payload.state;
    if (typeof eventState === "string" && TERMINAL_RUN_STATES.has(eventState)) {
      return { ...state, lastSeq: message.event.seq, assistantDrafts: {}, toolOutputDrafts: {} };
    }
    return { ...state, lastSeq: message.event.seq };
  }

  if (message.type === "assistant.delta") {
    return {
      ...state,
      assistantDrafts: {
        ...state.assistantDrafts,
        [message.draft_epoch]: `${state.assistantDrafts[message.draft_epoch] ?? ""}${message.text}`,
      },
    };
  }

  if (message.type === "tool.output.delta") {
    return {
      ...state,
      toolOutputDrafts: {
        ...state.toolOutputDrafts,
        [message.tool_call_id]: `${state.toolOutputDrafts[message.tool_call_id] ?? ""}${message.text}`,
      },
    };
  }

  return state;
}

export function sessionViewReducer(
  state: SessionViewState,
  action: SessionViewAction,
): SessionViewState {
  switch (action.type) {
    case "server.message":
      return reduceServerMessage(state, action.message);
    case "connection.changed":
      return { ...state, connection: action.connection };
    case "csrf.changed":
      return { ...state, csrfToken: action.csrfToken };
    case "draft.changed":
      return { ...state, draftText: action.draftText };
    case "session.selected":
      return createInitialSessionViewState();
  }
}
