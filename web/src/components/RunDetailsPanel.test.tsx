import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import type {
  ContextLoadDto,
  JsonValue,
  RunDto,
  RunTotalsDto,
  SessionSnapshotDto,
  SessionTotalsDto,
} from "../api/types";
import stylesheet from "../styles.css?raw";
import { RunDetailsPanel } from "./RunDetailsPanel";

afterEach(cleanup);

// vitest applies no CSS in jsdom, so the pill treatments are asserted against the
// stylesheet source, read with its comments removed.
const CSS_RULES = stylesheet.replaceAll(/\/\*[\s\S]*?\*\//g, "");

// The backend stores `asdict(AppSettings)`, so `config_snapshot.model` is a nested
// mapping and never a bare model name.
const NESTED_MODEL_CONFIG: Record<string, JsonValue> = {
  model: {
    base_url: "https://api.anthropic.com",
    model: "demo-model",
    api_key_env: "ANTHROPIC_API_KEY",
    context_window: 4096,
    max_output_tokens: 1024,
    stream: true,
  },
  agent: { max_rounds: 30 },
};

const ZERO_TOTALS: RunTotalsDto = {
  input_tokens: 0,
  output_tokens: 0,
  cache_creation_input_tokens: 0,
  cache_read_input_tokens: 0,
  round_count: 0,
  retry_count: 0,
};

const USED_TOTALS: RunTotalsDto = {
  input_tokens: 24,
  output_tokens: 12,
  cache_creation_input_tokens: 2,
  cache_read_input_tokens: 5,
  round_count: 3,
  retry_count: 2,
};

const SESSION_TOTALS: SessionTotalsDto = {
  run_count: 4,
  round_count: 3,
  retry_count: 2,
  input_tokens: 24,
  output_tokens: 12,
  cache_creation_input_tokens: 2,
  cache_read_input_tokens: 5,
};

const ZERO_SESSION_TOTALS: SessionTotalsDto = {
  run_count: 1,
  round_count: 0,
  retry_count: 0,
  input_tokens: 0,
  output_tokens: 0,
  cache_creation_input_tokens: 0,
  cache_read_input_tokens: 0,
};

/**
 * `active_run` is strictly non-terminal, so a live run carries neither a stop reason
 * nor an error kind nor a finish time.
 */
const LIVE_RUN: RunDto = {
  id: "run-1",
  session_id: "session-1",
  state: "model_streaming",
  stop_reason: null,
  error_kind: null,
  cancellation_requested_at: null,
  config_snapshot: NESTED_MODEL_CONFIG,
  started_at: "2026-08-28T00:00:00Z",
  finished_at: null,
  totals: ZERO_TOTALS,
  context: null,
};

/**
 * Every stop reason is written by the statement that makes a run terminal, so the
 * run published as `last_finished_run` always carries one plus a finish time.
 */
const FINISHED_RUN: RunDto = {
  ...LIVE_RUN,
  state: "failed",
  stop_reason: "retry_exhausted",
  error_kind: "retry_exhausted",
  finished_at: "2026-08-28T00:04:00Z",
};

function snapshotWith(runs: {
  active_run?: RunDto | null;
  last_finished_run?: RunDto | null;
  context_load?: ContextLoadDto | null;
  session_totals?: SessionTotalsDto | null;
}): SessionSnapshotDto {
  return {
    session: {
      id: "session-1",
      title: null,
      workspace_realpath: "/workspace",
      requires_recovery_ack: false,
      auto_approve: false,
      created_at: "2026-08-28T00:00:00Z",
      updated_at: "2026-08-28T00:00:00Z",
    },
    active_run: runs.active_run ?? null,
    last_finished_run: runs.last_finished_run ?? null,
    messages: [],
    tools: [],
    pending_approval: null,
    interrupted_banner: null,
    context_load: runs.context_load ?? null,
    session_totals: runs.session_totals ?? null,
    snapshot_seq: 1,
  };
}

function activeRunSnapshot(run: Partial<RunDto> = {}): SessionSnapshotDto {
  return snapshotWith({
    active_run: { ...LIVE_RUN, ...run },
    session_totals: SESSION_TOTALS,
  });
}

function finishedRunSnapshot(run: Partial<RunDto> = {}): SessionSnapshotDto {
  return snapshotWith({
    last_finished_run: { ...FINISHED_RUN, ...run },
    session_totals: SESSION_TOTALS,
  });
}

function rowValue(label: string): string | undefined {
  return rowCell(label)?.textContent ?? undefined;
}

function rowCell(label: string): HTMLElement | null {
  return (
    screen.getByText(label).parentElement?.querySelector<HTMLElement>("dd") ??
    null
  );
}

test("shows the run state, model, rounds, retries and stop reason the backend published", () => {
  const snapshot = finishedRunSnapshot();

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(rowValue("状态")).toBe("失败");
  expect(rowValue("运行 ID")).toBe("run-1");
  expect(rowValue("模型")).toBe("demo-model");
  // The counters are session-scoped: they sum every run of the conversation.
  expect(rowValue("运行次数")).toBe("4");
  expect(rowValue("轮次")).toBe("3");
  expect(rowValue("重试")).toBe("2");
  expect(rowValue("停止原因")).toBe("请求重试用尽");
  expect(rowValue("错误类型")).toBe("请求重试用尽");
});

test("presents a failed run's stop reason as a friendly cause, hint and raw code", () => {
  render(<RunDetailsPanel snapshot={finishedRunSnapshot()} />);

  expect(screen.getByText(/限流 429、服务端 5xx 或网络波动/)).not.toBeNull();
  expect(screen.getByText(/稍等片刻后重新发送消息即可重试/)).not.toBeNull();
  // The raw code stays visible so the operator can quote it verbatim.
  expect(screen.getByText("原始代码：retry_exhausted")).not.toBeNull();
  expect(screen.queryByText(/retry exhausted/)).toBeNull();
});

test("presents an internal error with the run id hint the operator needs to quote", () => {
  const snapshot = finishedRunSnapshot({
    state: "failed",
    stop_reason: "internal_error",
    error_kind: "internal_error",
  });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(rowValue("停止原因")).toBe("内部错误");
  expect(screen.getByText("原始代码：internal_error")).not.toBeNull();
  expect(screen.getByText(/查看运行 ID 后重试/)).not.toBeNull();
  expect(screen.getByText(/智能体内部发生错误/)).not.toBeNull();
});

test("presents a user stop as a neutral fact without a hint", () => {
  const snapshot = finishedRunSnapshot({
    state: "cancelled",
    stop_reason: "user_stop",
    error_kind: null,
  });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(rowValue("停止原因")).toBe("已手动停止");
  expect(screen.getByText("本次运行已按你的请求停止。")).not.toBeNull();
  expect(screen.getByText("原始代码：user_stop")).not.toBeNull();
  // A neutral outcome carries no remediation advice.
  expect(screen.queryByText(/建议|请检查|可以/)).toBeNull();
});

test("falls back gracefully for an unknown stop reason by quoting the raw code", () => {
  // Spec 5.2 fixes a minimum stop-reason vocabulary, not a closed one, so the
  // cast stands in for a value the contract does not name yet.
  const snapshot = finishedRunSnapshot({
    state: "failed",
    stop_reason: "totally_unknown" as RunDto["stop_reason"],
    error_kind: null,
  });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(rowValue("停止原因")).toBe("运行异常结束");
  expect(screen.getByText(/原始代码：totally_unknown/)).not.toBeNull();
});

test("keeps the finished run and its stop reason visible once no run is active", () => {
  render(<RunDetailsPanel snapshot={finishedRunSnapshot()} />);

  expect(
    screen.getByText("上一个完成的任务。当前没有运行中的任务，计数为会话累计。"),
  ).not.toBeNull();
  expect(screen.queryByText("当前没有运行中的任务。")).toBeNull();
  expect(rowValue("状态")).toBe("失败");
  expect(rowValue("停止原因")).toBe("请求重试用尽");
  expect(rowValue("结束时间")).toBe(
    new Date("2026-08-28T00:04:00Z").toLocaleString(),
  );
});

test("keeps the session counters between runs: no active run never wipes them", () => {
  // Between requests the snapshot carries no run objects at all, yet the
  // conversation's accumulated footprint must stay on the rail.
  const snapshot = snapshotWith({
    context_load: { agents_md_path: "AGENTS.md", skills: ["git-helper"] },
    session_totals: SESSION_TOTALS,
  });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(screen.getByText("会话累计。当前没有运行中的任务。")).not.toBeNull();
  expect(rowValue("运行次数")).toBe("4");
  expect(rowValue("轮次")).toBe("3");
  expect(rowValue("重试")).toBe("2");
  expect(rowValue("累计 Token")).toContain("输入 24");
  expect(
    screen.getByRole("region", { name: "已加载上下文" }),
  ).not.toBeNull();
});

test("presents a live run as the active one instead of the finished run", () => {
  const snapshot = snapshotWith({
    active_run: { ...LIVE_RUN, id: "run-2" },
    last_finished_run: FINISHED_RUN,
    session_totals: SESSION_TOTALS,
  });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(screen.getByText("运行中的任务，计数为会话累计。")).not.toBeNull();
  expect(screen.queryByText(/上一个完成的任务/)).toBeNull();
  expect(rowValue("运行 ID")).toBe("run-2");
  expect(rowValue("状态")).toBe("生成中");
  expect(screen.queryByText("停止原因")).toBeNull();
  expect(screen.queryByText("结束时间")).toBeNull();
  expect(screen.queryByText("原始代码：")).toBeNull();
});

test("labels the token totals as cumulative known usage across the session", () => {
  const snapshot = activeRunSnapshot();

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(
    rowCell("累计 Token")?.querySelector(".run-token-metrics")?.textContent,
  ).toBe("输入 24 · 输出 12 · 缓存写入 2 · 缓存读取 5");
  expect(
    screen.getByText(/会话内全部运行累计的已知用量，非当前上下文占用/),
  ).not.toBeNull();
});

test("each token metric wraps as one unbreakable unit in the narrow rail", () => {
  const snapshot = activeRunSnapshot();

  render(<RunDetailsPanel snapshot={snapshot} />);

  const metrics = screen
    .getAllByText(/^(输入|输出|缓存写入|缓存读取) \d+$/)
    .map((element) => element.className);
  expect(metrics).toHaveLength(4);
  // Every metric carries the nowrap unit class, and the stylesheet keeps it.
  for (const className of metrics) {
    expect(className).toContain("run-token-metric");
  }
  const unitRule = declarationsFor(".run-token-metric");
  expect(unitRule?.["white-space"]).toBe("nowrap");
  expect(unitRule?.display).toBe("inline-block");
});

test("lists the AGENTS.md and fully-read skills the session loaded", () => {
  const snapshot = activeRunSnapshot();
  snapshot.context_load = {
    agents_md_path: "AGENTS.md",
    skills: ["web-evolve.md", "debug-loop.md"],
  };

  render(<RunDetailsPanel snapshot={snapshot} />);

  const load = screen.getByRole("region", { name: "已加载上下文" });
  expect(load.textContent).toContain("AGENTS.md：AGENTS.md");
  expect(load.querySelectorAll("li").length).toBe(2);
  expect(load.textContent).toContain("web-evolve.md");
  expect(load.textContent).toContain("debug-loop.md");
});

test("states the absences of AGENTS.md and skills instead of hiding the section", () => {
  const snapshot = activeRunSnapshot();
  snapshot.context_load = { agents_md_path: null, skills: [] };

  render(<RunDetailsPanel snapshot={snapshot} />);

  const load = screen.getByRole("region", { name: "已加载上下文" });
  expect(load.textContent).toContain("本会话未读取 AGENTS.md");
  expect(load.textContent).toContain("本会话未读取技能。");
});

test("hides the context-load section before the session ever ran", () => {
  render(<RunDetailsPanel snapshot={snapshotWith({})} />);

  expect(screen.queryByRole("region", { name: "已加载上下文" })).toBeNull();
});

test("never presents a context-occupancy figure the run row cannot support", () => {
  const snapshot = activeRunSnapshot();

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(screen.queryByText("上下文占用率")).toBeNull();
  expect(screen.queryByText(/%/)).toBeNull();
  expect(screen.queryByText("4096")).toBeNull();
});

test("omits run-detail rows when the backend did not provide their values", () => {
  // A run without published session totals shows identity rows only.
  const snapshot = snapshotWith({
    active_run: {
      ...LIVE_RUN,
      state: "model_streaming",
      config_snapshot: { model: { context_window: 4096 } },
    },
  });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(screen.queryByText("模型")).toBeNull();
  expect(screen.queryByText("上下文窗口")).toBeNull();
  expect(screen.queryByText("停止原因")).toBeNull();
  expect(screen.queryByText("错误类型")).toBeNull();
  expect(screen.queryByText("运行次数")).toBeNull();
  expect(screen.queryByText("轮次")).toBeNull();
  expect(screen.queryByText("重试")).toBeNull();
});

test("zeroed session counters are the published fact of a young session", () => {
  const snapshot = snapshotWith({
    active_run: {
      ...LIVE_RUN,
      state: "model_streaming",
      config_snapshot: { model: { context_window: 4096 } },
    },
    session_totals: ZERO_SESSION_TOTALS,
  });

  render(<RunDetailsPanel snapshot={snapshot} />);

  expect(rowValue("运行次数")).toBe("1");
  // Rounds and retries are stored counters, so zero is the published fact.
  expect(rowValue("轮次")).toBe("0");
  expect(rowValue("重试")).toBe("0");
});

test("reports that there is no active run when the snapshot has none", () => {
  render(<RunDetailsPanel snapshot={null} />);

  expect(screen.getByText("当前没有运行中的任务。")).not.toBeNull();
});

test("reports no run at all when the session never finished one", () => {
  render(<RunDetailsPanel snapshot={snapshotWith({})} />);

  expect(screen.getByText("当前没有运行中的任务。")).not.toBeNull();
  expect(screen.queryByText("状态")).toBeNull();
});

// Spec 5.2 pairs each terminal state with the stop reasons that can produce it.
const TERMINAL_RUNS = [
  ["completed", "completed", "已完成"],
  ["stopped", "max_rounds", "已停止"],
  ["cancelled", "user_stop", "已取消"],
  ["failed", "retry_exhausted", "失败"],
  ["interrupted", "server_restart", "已中断"],
] as const;

test.each(TERMINAL_RUNS)(
  "marks a %s run with its own state class",
  (state, stopReason, label) => {
    const snapshot = finishedRunSnapshot({ state, stop_reason: stopReason });

    render(<RunDetailsPanel snapshot={snapshot} />);

    const pill = rowCell("状态");
    expect(pill?.textContent).toBe(label);
    expect(pill?.className).toBe(`run-state run-state-${state}`);
  },
);

test("gives every terminal run state its own pill treatment", () => {
  const treatments = TERMINAL_RUNS.map(([state]) => {
    const selector = `.run-state-${state}`;
    const declarations = declarationsFor(selector);
    expect(declarations, selector).not.toBeNull();
    // Without its own fill the pill is the shared neutral one.
    expect(Object.keys(declarations ?? {}), selector).toContain("background");
    // `.run-details-list dd` outranks these single-class rules for `color`, so the
    // fill and the border are what separate the states on screen.
    return JSON.stringify([
      declarations?.background,
      declarations?.["border-color"],
      declarations?.["border-style"],
    ]);
  });

  expect(new Set(treatments).size).toBe(TERMINAL_RUNS.length);
});

function declarationsFor(selector: string): Record<string, string> | null {
  for (const rule of CSS_RULES.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = rule[1].split(",").map((item) => item.trim());
    if (!selectors.includes(selector)) continue;
    const declarations: Record<string, string> = {};
    for (const declaration of rule[2].split(";")) {
      const separator = declaration.indexOf(":");
      if (separator === -1) continue;
      const property = declaration.slice(0, separator).trim();
      declarations[property] = declaration.slice(separator + 1).trim();
    }
    return declarations;
  }
  return null;
}
