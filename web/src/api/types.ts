export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

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
] as const;
export type StopReason = (typeof STOP_REASONS)[number];

export const APPROVAL_STATUSES = ["pending", "approved", "rejected", "cancelled"] as const;
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
  | "context_overflow";
export type MessageStatus = "pending_tools" | "committed" | "interrupted";

export type SessionDto = {
  id: string;
  title: string | null;
  workspace_realpath: string;
  requires_recovery_ack: boolean;
  created_at: string;
  updated_at: string;
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
};

export type TextPartDto = { type: "text"; text: string };
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
export type MessagePartDto = TextPartDto | ToolUsePartDto | ToolResultPartDto;

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

export type SessionSnapshotDto = {
  session: SessionDto;
  active_run: RunDto | null;
  messages: MessageDto[];
  tools: ToolExecutionDto[];
  pending_approval: PendingApprovalDto | null;
  interrupted_banner: InterruptedBannerDto | null;
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

export type ClientCommand =
  | { type: "session.subscribe"; client_command_id: string; session_id: string; payload: Record<string, never> }
  | { type: "run.start"; client_command_id: string; session_id: string; payload: { content: string } }
  | { type: "run.stop"; client_command_id: string; session_id: string; payload: { run_id: string } }
  | {
      type: "approval.resolve";
      client_command_id: string;
      session_id: string;
      payload: { run_id: string; tool_call_id: string; decision: ApprovalDecision };
    }
  | { type: "session.ack_recovery"; client_command_id: string; session_id: string; payload: Record<string, never> };

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
  | { type: "snapshot"; client_command_id: string; session_id: string; snapshot: SessionSnapshotDto }
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
      type: "tool.output.delta";
      session_id: string;
      run_id: string;
      draft_epoch: string;
      tool_call_id: string;
      text: string;
    };

export type BootstrapDto = { csrf_token: string; websocket_url: string };
