import type { RunDto } from "./types";

/**
 * Friendly presentation of how one run ended. `code` is the raw snake_case value
 * the API delivered and is always surfaced somewhere, so an unmapped value can
 * never hide what actually happened; `isError` is false for the neutral outcomes
 * (completion, user stop, pause) that are facts, not failures.
 */
export type StopOutcome = {
  code: string;
  title: string;
  description: string;
  hint: string | null;
  isError: boolean;
};

type OutcomeEntry = Omit<StopOutcome, "code">;

/** The data the timeline's failure banner needs; derived from a finished run. */
export type RunFailure = {
  runId: string;
  code: string;
  title: string;
  description: string;
  hint: string | null;
  retryCount: number;
};

/**
 * Every stop reason and error kind the backend can publish (spec 5.2), in
 * 简体中文. The two vocabularies overlap heavily, so one table serves both:
 * a stop reason names how the run ended, an error kind names the failure class,
 * and only the wording of the surrounding UI differs.
 */
const OUTCOMES: Record<string, OutcomeEntry> = {
  completed: {
    title: "运行完成",
    description: "本轮任务已正常完成。",
    hint: null,
    isError: false,
  },
  user_stop: {
    title: "已手动停止",
    description: "本次运行已按你的请求停止。",
    hint: null,
    isError: false,
  },
  max_rounds: {
    title: "已达到轮次上限",
    description: "本次运行达到配置的最大对话轮数（max_rounds）后停止。",
    hint: "可以发送新消息让智能体继续，或在配置中调大 max_rounds。",
    isError: true,
  },
  doom_loop: {
    title: "检测到重复循环",
    description: "智能体在连续重复相似的操作，运行已停止以避免空转。",
    hint: "请检查任务描述是否清晰，或换一种说法重新下达指令。",
    isError: true,
  },
  empty_response: {
    title: "模型返回了空响应",
    description: "模型本轮没有返回任何内容，运行无法继续。",
    hint: "这通常是瞬时问题，直接重发消息一般即可恢复。",
    isError: true,
  },
  output_truncated: {
    title: "输出被截断",
    description: "模型输出达到单次输出 token 上限，内容被截断。",
    hint: "可以让模型接着上一段继续，或在配置中调大 max_output_tokens。",
    isError: true,
  },
  incomplete_tool_call: {
    title: "工具调用不完整",
    description: "模型的输出包含不完整的工具调用，无法解析执行。",
    hint: "这通常是模型的瞬时问题，重发消息一般即可恢复；若频繁出现请更换模型。",
    isError: true,
  },
  auth_error: {
    title: "鉴权失败",
    description: "模型服务拒绝了请求：API 密钥无效或已过期。",
    hint: "请检查 API 密钥环境变量是否配置正确，然后重新发送。",
    isError: true,
  },
  config_error: {
    title: "配置错误",
    description: "模型或服务配置有误，请求无法发出。",
    hint: "请检查 config.toml 中的模型配置（base_url、model、api_key_env 等）。",
    isError: true,
  },
  retry_exhausted: {
    title: "请求重试用尽",
    description:
      "模型服务持续不可用（可能是限流 429、服务端 5xx 或网络波动），自动重试后仍然失败。",
    hint: "稍等片刻后重新发送消息即可重试；若持续失败请检查网络或模型服务状态。",
    isError: true,
  },
  context_overflow: {
    title: "上下文超出模型窗口",
    description: "对话上下文长度超过模型的上下文窗口，本轮运行无法继续。",
    hint: "建议开启新会话继续，或改用上下文窗口更大的模型。",
    isError: true,
  },
  model_refusal: {
    title: "模型拒绝执行",
    description: "模型出于安全策略拒绝了本次请求。",
    hint: "请调整任务表述，避免模型可能判定为敏感或违规的内容。",
    isError: true,
  },
  pause_turn: {
    title: "模型请求暂停",
    description: "模型暂停了本轮输出，等待继续。",
    hint: null,
    isError: false,
  },
  server_restart: {
    title: "服务重启导致中断",
    description: "后端服务在运行期间重启，本次运行被中断。",
    hint: "请先检查工作区与后台进程，确认无误后继续对话。",
    isError: true,
  },
  model_protocol_error: {
    title: "模型输出不符合协议",
    description: "模型返回的内容不符合 API 协议，无法解析。",
    hint: "这通常是模型的瞬时问题，重发消息一般即可恢复；若频繁出现请更换模型。",
    isError: true,
  },
  internal_error: {
    title: "内部错误",
    description: "智能体内部发生错误，本次运行已终止。",
    hint: "可在右侧“运行详情”中查看运行 ID 后重试；若持续出现请查看服务端日志。",
    isError: true,
  },
};

const UNKNOWN_OUTCOME: OutcomeEntry = {
  title: "运行异常结束",
  description: "运行以未知原因结束。",
  hint: "可以重新发送消息重试；若持续出现请查看服务端日志。",
  isError: true,
};

/**
 * Map a terminal run's `(stop_reason, error_kind)` pair to its friendly record.
 * The stop reason is the primary key; an unknown stop reason falls back to the
 * error kind (they overlap heavily), and an unknown pair degrades to a generic
 * failure that still quotes the raw code instead of a blank panel.
 */
export function describeStopOutcome(
  stopReason: string | null,
  errorKind: string | null = null,
): StopOutcome | null {
  if (stopReason !== null && stopReason in OUTCOMES) {
    return { code: stopReason, ...OUTCOMES[stopReason] };
  }
  if (errorKind !== null && errorKind in OUTCOMES) {
    return { code: errorKind, ...OUTCOMES[errorKind] };
  }
  const code = stopReason ?? errorKind;
  if (code === null) return null;
  // The raw code stays on the record (`code`) so the panel can quote it; the
  // description itself never duplicates it.
  return { code, ...UNKNOWN_OUTCOME };
}

/**
 * Whether the finished run deserves the timeline's failure banner. Only a run
 * the backend marked `failed` is a failure: a user stop lands in `cancelled`,
 * a soft limit lands in `stopped`, and both stay banner-free, as does a normal
 * completion.
 */
export function failureBannerFor(run: RunDto | null): RunFailure | null {
  if (run === null || run.state !== "failed") return null;
  const outcome = describeStopOutcome(run.stop_reason, run.error_kind);
  if (outcome === null || !outcome.isError) return null;
  return {
    runId: run.id,
    code: outcome.code,
    title: outcome.title,
    description: outcome.description,
    hint: outcome.hint,
    retryCount: run.totals.retry_count,
  };
}
