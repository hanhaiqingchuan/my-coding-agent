import type { RunState, ToolExecutionState } from "./types";
import type { ConnectionState } from "../features/sessions/sessionReducer";

/**
 * 中文标签 for the enum values the API delivers as snake_case identifiers.
 * The raw values stay the wire format and the CSS class names; only what the
 * user reads is translated, and an unexpected value degrades to the raw code
 * instead of a blank label.
 */

export const CONNECTION_LABELS: Record<ConnectionState, string> = {
  connecting: "连接中",
  connected: "已连接",
  reconnecting: "重连中",
  offline: "离线",
};

export const RUN_STATE_LABELS: Record<RunState, string> = {
  starting: "启动中",
  building_context: "构建上下文",
  compacting: "压缩上下文",
  model_streaming: "生成中",
  retry_wait: "重试等待",
  awaiting_approval: "等待审批",
  tool_running: "工具运行中",
  cancelling: "正在取消",
  completed: "已完成",
  stopped: "已停止",
  cancelled: "已取消",
  failed: "失败",
  interrupted: "已中断",
};

export const TOOL_STATE_LABELS: Record<ToolExecutionState, string> = {
  queued: "已排队",
  awaiting_approval: "等待审批",
  running: "运行中",
  succeeded: "成功",
  failed: "失败",
  rejected: "已拒绝",
  cancelled: "已取消",
  skipped: "已跳过",
  unknown: "未知",
};

export function connectionLabel(state: ConnectionState): string {
  return CONNECTION_LABELS[state] ?? state;
}

export function runStateLabel(state: RunState): string {
  return RUN_STATE_LABELS[state] ?? state;
}

export function toolStateLabel(state: ToolExecutionState): string {
  return TOOL_STATE_LABELS[state] ?? state;
}

export function roleLabel(role: string): string {
  if (role === "user") return "用户";
  if (role === "assistant") return "助手";
  return role;
}
