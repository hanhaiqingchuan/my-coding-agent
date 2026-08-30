import type {
  DurableEvent,
  ServerMessage,
  SessionSnapshotDto,
} from "../../api/types";

const TERMINAL_RUN_STATES = new Set([
  "completed",
  "stopped",
  "cancelled",
  "failed",
  "interrupted",
]);
const DRAFT_COMMIT_EVENTS = new Set([
  "assistant.turn_committed",
  "assistant.interrupted",
  "tool.group_settled",
]);

export type ConnectionState =
  "connecting" | "connected" | "reconnecting" | "offline";

/** One round's streamed reasoning: `closed` is the backend's auto-collapse signal. */
export type ThinkingDraft = { text: string; closed: boolean };

/**
 * Compaction visibility from durable events: `running` while the compactor works,
 * `finished` once `compaction.finished` reports the before/after estimates. Covers
 * both run-internal auto-compaction and the `/compact` maintenance command.
 */
export type CompactionStatus =
  | { phase: "running" }
  | {
      phase: "finished";
      beforeTokens: number;
      afterTokens: number;
      errorCode?: string;
    };

export type SessionViewState = {
  snapshot: SessionSnapshotDto | null;
  draftText: string;
  connection: ConnectionState;
  lastSeq: number;
  csrfToken: string | null;
  assistantDrafts: Record<string, string>;
  thinkingDrafts: Record<string, ThinkingDraft>;
  toolOutputDrafts: Record<string, string>;
  compaction: CompactionStatus | null;
  /** The last rejected command (e.g. a /compact with nothing to compact), shown until dismissed. */
  commandError: { code: string; message: string } | null;
};

export type SessionViewAction =
  | { type: "server.message"; message: ServerMessage }
  | { type: "snapshot.refreshed"; snapshot: SessionSnapshotDto }
  | { type: "connection.changed"; connection: ConnectionState }
  | { type: "csrf.changed"; csrfToken: string | null }
  | { type: "draft.changed"; draftText: string }
  | { type: "commandError.dismissed" }
  | { type: "session.selected" };

export function createInitialSessionViewState(): SessionViewState {
  return {
    snapshot: null,
    draftText: "",
    connection: "offline",
    lastSeq: 0,
    csrfToken: null,
    assistantDrafts: {},
    thinkingDrafts: {},
    toolOutputDrafts: {},
    compaction: null,
    commandError: null,
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
      thinkingDrafts: {},
      toolOutputDrafts: {},
    };
  }

  if (message.type === "durable") {
    if (message.event.seq <= state.lastSeq) {
      return state;
    }
    const eventState = message.event.payload.state;
    const base =
      (typeof eventState === "string" && TERMINAL_RUN_STATES.has(eventState)) ||
      DRAFT_COMMIT_EVENTS.has(message.event.type)
        ? {
            ...state,
            lastSeq: message.event.seq,
            assistantDrafts: {},
            thinkingDrafts: {},
            toolOutputDrafts: {},
          }
        : { ...state, lastSeq: message.event.seq };
    const compaction = compactionFromEvent(message.event);
    // A finished chip only announces the last compaction; a new run supersedes it.
    if (message.event.type === "run.started") {
      return { ...base, compaction: null };
    }
    return compaction === null ? base : { ...base, compaction };
  }

  if (message.type === "command_error") {
    return {
      ...state,
      commandError: { code: message.code, message: message.message },
    };
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

  if (message.type === "assistant.thinking.delta") {
    // A delta always re-opens the draft: a second thinking block in the same
    // round streams into the same epoch after the previous block closed.
    return {
      ...state,
      thinkingDrafts: {
        ...state.thinkingDrafts,
        [message.draft_epoch]: {
          text: `${state.thinkingDrafts[message.draft_epoch]?.text ?? ""}${message.text}`,
          closed: false,
        },
      },
    };
  }

  if (message.type === "assistant.thinking.closed") {
    const current = state.thinkingDrafts[message.draft_epoch];
    // Transient events are dropped on reconnect, so a close can arrive without
    // any text; there is nothing to collapse then.
    if (current === undefined || current.closed) return state;
    return {
      ...state,
      thinkingDrafts: {
        ...state.thinkingDrafts,
        [message.draft_epoch]: { text: current.text, closed: true },
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
    case "snapshot.refreshed": {
      if (action.snapshot.snapshot_seq < state.lastSeq) {
        return state;
      }
      const activeRun = action.snapshot.active_run;
      const runStillProducing =
        activeRun !== null && !TERMINAL_RUN_STATES.has(activeRun.state);
      return {
        ...state,
        snapshot: action.snapshot,
        lastSeq: action.snapshot.snapshot_seq,
        assistantDrafts: runStillProducing ? state.assistantDrafts : {},
        thinkingDrafts: runStillProducing ? state.thinkingDrafts : {},
        toolOutputDrafts: runStillProducing ? state.toolOutputDrafts : {},
      };
    }
    case "connection.changed":
      return { ...state, connection: action.connection };
    case "csrf.changed":
      return { ...state, csrfToken: action.csrfToken };
    case "draft.changed":
      return { ...state, draftText: action.draftText };
    case "commandError.dismissed":
      return { ...state, commandError: null };
    case "session.selected":
      return createInitialSessionViewState();
  }
}

/**
 * Translate one durable event into compaction visibility, or `null` when the event
 * says nothing about compaction. The estimates come straight from the backend's
 * payload; a malformed number never fabricates a figure and only degrades the chip
 * to the running/finished phase statement.
 */
function compactionFromEvent(event: DurableEvent): CompactionStatus | null {
  if (event.type === "compaction.started") {
    return { phase: "running" };
  }
  if (event.type === "compaction.finished") {
    const before = event.payload.before_estimated_tokens;
    const after = event.payload.after_estimated_tokens;
    const error = event.payload.error;
    const errorCode =
      typeof error === "object" && error !== null && "code" in error
        ? String((error as { code: unknown }).code)
        : undefined;
    if (typeof before === "number" && typeof after === "number") {
      return {
        phase: "finished",
        beforeTokens: before,
        afterTokens: after,
        errorCode,
      };
    }
    return { phase: "finished", beforeTokens: 0, afterTokens: 0, errorCode };
  }
  return null;
}
