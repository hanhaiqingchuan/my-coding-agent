export type JsonValue =
  string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export const RUN_STATES = [
  "starting",
  "building_context",
  "compacting",
  "model_streaming",
  "retry_wait",
  "awaiting_approval",
  "tool_running",
  "cancelling",
  "completed",
  "stopped",
  "cancelled",
  "failed",
  "interrupted",
] as const;
export type RunState = (typeof RUN_STATES)[number];

export const STOP_REASONS = [
  "completed",
  "user_stop",
  "max_rounds",
  "doom_loop",
  "empty_response",
  "output_truncated",
  "incomplete_tool_call",
  "auth_error",
  "config_error",
  "retry_exhausted",
  "context_overflow",
  "model_refusal",
  "pause_turn",
  "server_restart",
  "model_protocol_error",
  "internal_error",
] as const;
export type StopReason = (typeof STOP_REASONS)[number];

export const APPROVAL_STATUSES = [
  "pending",
  "approved",
  "rejected",
  "cancelled",
] as const;
export type ApprovalStatus = (typeof APPROVAL_STATUSES)[number];

export const APPROVAL_DECISIONS = ["approve", "reject"] as const;
export type ApprovalDecision = (typeof APPROVAL_DECISIONS)[number];

export const TOOL_EXECUTION_STATES = [
  "queued",
  "awaiting_approval",
  "running",
  "succeeded",
  "failed",
  "rejected",
  "cancelled",
  "skipped",
  "unknown",
] as const;
export type ToolExecutionState = (typeof TOOL_EXECUTION_STATES)[number];

export type ErrorKind =
  | "model_protocol_error"
  | "auth_error"
  | "config_error"
  | "retry_exhausted"
  | "context_overflow"
  | "internal_error";
export type MessageStatus = "pending_tools" | "committed" | "interrupted";

export type SessionDto = {
  id: string;
  title: string | null;
  workspace_realpath: string;
  requires_recovery_ack: boolean;
  /** Per-session approval mode (spec 13.4); server-persisted, default interactive. */
  auto_approve: boolean;
  created_at: string;
  updated_at: string;
};

/**
 * Cumulative counters SQLite sums over the whole run. The token fields are sums of the
 * usage the provider actually reported, so they describe known usage across rounds and
 * never the current context-window occupancy.
 */
export type RunTotalsDto = {
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  round_count: number;
  retry_count: number;
};

/** The run's latest context estimate; `estimated_tokens / available_tokens` drives the bar. */
export type RunContextDto = {
  estimated_tokens: number;
  available_tokens: number;
  window_tokens: number;
};

export type RunDto = {
  id: string;
  session_id: string;
  state: RunState;
  stop_reason: StopReason | null;
  error_kind: ErrorKind | null;
  cancellation_requested_at: string | null;
  config_snapshot: Record<string, JsonValue>;
  started_at: string;
  finished_at: string | null;
  totals: RunTotalsDto;
  context: RunContextDto | null;
};

export type TextPartDto = { type: "text"; text: string };
/** Provider reasoning, display-only: collapsed in history, never fed back to the model. */
export type ThinkingPartDto = { type: "thinking"; text: string };
export type ToolUsePartDto = {
  type: "tool_use";
  id: string;
  name: string;
  input: Record<string, JsonValue>;
};
export type ToolErrorDto = { code: string; message: string };
export type ToolResultPartDto = {
  type: "tool_result";
  tool_call_id: string;
  content: string;
  ok: boolean;
  error: ToolErrorDto | null;
  data: Record<string, JsonValue>;
  truncated: boolean;
};
export type MessagePartDto =
  TextPartDto | ThinkingPartDto | ToolUsePartDto | ToolResultPartDto;

export type MessageDto = {
  id: string;
  session_id: string;
  run_id: string | null;
  seq: number;
  role: string;
  parts: MessagePartDto[];
  status: MessageStatus;
  tool_call_id: string | null;
};

export type ToolExecutionDto = {
  tool_call_id: string;
  run_id: string;
  assistant_message_id: string;
  call_order: number;
  name: string;
  input: Record<string, JsonValue>;
  requires_approval: boolean;
  approval_status: ApprovalStatus;
  approval_decision: ApprovalDecision | null;
  approval_decided_at: string | null;
  execution_state: ToolExecutionState;
  result: ToolResultPartDto | null;
  duration_ms: number | null;
};

export type PendingApprovalDto = {
  run_id: string;
  tool_call_id: string;
  name: string;
  input: Record<string, JsonValue>;
  target: string | null;
  preview: string | null;
  metadata: Record<string, JsonValue>;
};

export type InterruptedBannerDto = {
  run_id: string;
  stop_reason: StopReason;
  requires_recovery_ack: boolean;
};

/**
 * What the focus run (active, else last finished) loaded into its system context
 * (spec 13.5). `skills` lists only skills the model pulled through the skill tool,
 * never the discovered index.
 */
export type ContextLoadDto = {
  agents_md_path: string | null;
  skills: string[];
};

export type SessionSnapshotDto = {
  session: SessionDto;
  active_run: RunDto | null;
  last_finished_run: RunDto | null;
  messages: MessageDto[];
  tools: ToolExecutionDto[];
  pending_approval: PendingApprovalDto | null;
  interrupted_banner: InterruptedBannerDto | null;
  context_load: ContextLoadDto | null;
  snapshot_seq: number;
};

export type DurableEvent = {
  seq: number;
  session_id: string;
  run_id: string | null;
  type: string;
  payload: Record<string, JsonValue>;
  created_at: string;
};

export const REQUIRED_DTO_FIELDS = {
  RunDto: [
    "id",
    "session_id",
    "state",
    "stop_reason",
    "error_kind",
    "cancellation_requested_at",
    "config_snapshot",
    "started_at",
    "finished_at",
    "totals",
    "context",
  ],
  RunTotalsDto: [
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "round_count",
    "retry_count",
  ],
  MessageDto: [
    "id",
    "session_id",
    "run_id",
    "seq",
    "role",
    "parts",
    "status",
    "tool_call_id",
  ],
  ToolExecutionDto: [
    "tool_call_id",
    "run_id",
    "assistant_message_id",
    "call_order",
    "name",
    "input",
    "requires_approval",
    "approval_status",
    "approval_decision",
    "approval_decided_at",
    "execution_state",
    "result",
    "duration_ms",
  ],
  PendingApprovalDto: [
    "run_id",
    "tool_call_id",
    "name",
    "input",
    "target",
    "preview",
    "metadata",
  ],
  SessionSnapshotDto: [
    "session",
    "active_run",
    "last_finished_run",
    "messages",
    "tools",
    "pending_approval",
    "interrupted_banner",
    "context_load",
    "snapshot_seq",
  ],
  DurableEvent: [
    "seq",
    "session_id",
    "run_id",
    "type",
    "payload",
    "created_at",
  ],
} as const;

type RequiredDtoMap = {
  RunDto: RunDto;
  RunTotalsDto: RunTotalsDto;
  MessageDto: MessageDto;
  ToolExecutionDto: ToolExecutionDto;
  PendingApprovalDto: PendingApprovalDto;
  SessionSnapshotDto: SessionSnapshotDto;
  DurableEvent: DurableEvent;
};

type ExactFieldSet<Dto, Fields extends readonly PropertyKey[]> =
  Exclude<keyof Dto, Fields[number]> extends never
    ? Exclude<Fields[number], keyof Dto> extends never
      ? true
      : never
    : never;

const requiredDtoFieldsMatchTypes: {
  [Name in keyof RequiredDtoMap]: ExactFieldSet<
    RequiredDtoMap[Name],
    (typeof REQUIRED_DTO_FIELDS)[Name]
  >;
} = {
  RunDto: true,
  RunTotalsDto: true,
  MessageDto: true,
  ToolExecutionDto: true,
  PendingApprovalDto: true,
  SessionSnapshotDto: true,
  DurableEvent: true,
};
void requiredDtoFieldsMatchTypes;

export type ClientCommand =
  | {
      type: "session.subscribe";
      client_command_id: string;
      session_id: string;
      payload: Record<string, never>;
    }
  | {
      type: "run.start";
      client_command_id: string;
      session_id: string;
      payload: { content: string };
    }
  | {
      type: "run.stop";
      client_command_id: string;
      session_id: string;
      payload: { run_id: string };
    }
  | {
      type: "approval.resolve";
      client_command_id: string;
      session_id: string;
      payload: {
        run_id: string;
        tool_call_id: string;
        decision: ApprovalDecision;
      };
    }
  | {
      type: "session.ack_recovery";
      client_command_id: string;
      session_id: string;
      payload: Record<string, never>;
    }
  | {
      /** Maintenance compaction with no active run; outcome via compaction.* events. */
      type: "session.compact";
      client_command_id: string;
      session_id: string;
      payload: Record<string, never>;
    }
  | {
      /** Wipe the conversation history; the session itself stays. */
      type: "session.clear";
      client_command_id: string;
      session_id: string;
      payload: Record<string, never>;
    }
  | {
      /** Persist the per-session approval mode (spec 13.4); audited durably. */
      type: "session.set_approval_mode";
      client_command_id: string;
      session_id: string;
      payload: { auto_approve: boolean };
    };

export type ServerMessage =
  | {
      type: "ack";
      client_command_id: string;
      session_id: string;
      command_type: string;
      status: "completed";
      resource_id: string;
    }
  | {
      type: "command_error";
      client_command_id: string | null;
      session_id: string | null;
      code: string;
      message: string;
    }
  | {
      type: "snapshot";
      client_command_id: string;
      session_id: string;
      snapshot: SessionSnapshotDto;
    }
  | { type: "durable"; event: DurableEvent }
  | {
      type: "assistant.delta";
      session_id: string;
      run_id: string;
      draft_epoch: string;
      index: number;
      text: string;
    }
  | {
      /** Transient reasoning increment; dropped on reconnect, never durable. */
      type: "assistant.thinking.delta";
      session_id: string;
      run_id: string;
      draft_epoch: string;
      index: number;
      text: string;
    }
  | {
      /** One thinking block finished; the display-only auto-collapse signal. */
      type: "assistant.thinking.closed";
      session_id: string;
      run_id: string;
      draft_epoch: string;
      index: number;
    }
  | {
      type: "tool.output.delta";
      session_id: string;
      run_id: string;
      draft_epoch: string;
      tool_call_id: string;
      text: string;
    };

export type BootstrapDto = { csrf_token: string; websocket_url: string };
