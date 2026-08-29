import type { JsonValue } from "../../api/types";

/** The three judge dimensions, in the fixed order the backend and UI share. */
export const SCORE_NAMES = [
  "task_completion",
  "process_quality",
  "communication",
] as const;
export type ScoreName = (typeof SCORE_NAMES)[number];

export const SCORE_LABELS: Record<ScoreName, string> = {
  task_completion: "Task completion",
  process_quality: "Process quality",
  communication: "Communication",
};

export type JudgeScores = {
  task_completion: number | null;
  process_quality: number | null;
  communication: number | null;
};

/** One campaign's headline numbers, exactly as the history index scans it. */
export type CampaignSummaryDto = {
  campaign_id: string | null;
  directory: string;
  started_at: string | null;
  finished_at: string | null;
  task_count: number;
  started_runs: number;
  valid_runs: number;
  strict_success_runs: number;
  strict_success_rate: number | null;
  judged_runs: number;
  judge_error_runs: number;
  judge_means: Record<string, number | null>;
  model_name: string | null;
  judge_model: string | null;
  corrupt: boolean;
  note: string | null;
};

/** One run's deterministic metrics: rounds, tool calls, tokens, durations, outcome. */
export type RunRowDto = {
  task_id: string;
  repeat: number;
  category: string;
  outcome: string;
  strict_success: boolean;
  artifact_correct: boolean;
  state: string | null;
  stop_reason: string | null;
  failure_stage: string | null;
  failure_kind: string | null;
  rounds: number | null;
  tool_calls: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  agent_ms: number | null;
  total_ms: number | null;
  judge_scores: JudgeScores | null;
  judge_error: boolean;
};

export type TaskRunsDto = {
  task_id: string;
  category: string;
  runs: RunRowDto[];
};

export type CampaignAggregatesDto = {
  started_runs: number;
  valid_runs: number;
  strict_success_runs: number;
  artifact_correct_runs: number;
  task_completion_rate: number | null;
  robust_task_count: number;
  total_input_tokens: number | null;
  total_output_tokens: number | null;
  total_main_requests: number;
  total_tool_calls: number;
  judged_runs: number;
  judge_error_runs: number;
  judge_means: Record<string, number | null>;
  failure_kinds: Record<string, number>;
};

export type CampaignDetailDto = {
  summary: CampaignSummaryDto;
  aggregates: CampaignAggregatesDto | null;
  tasks: TaskRunsDto[];
  note: string | null;
};

/** The verbatim judgement-v1 record of one run. */
export type JudgementDto = {
  schema_version: "judgement-v1";
  campaign_id: string | null;
  task_id: string;
  repeat: number;
  judge_model: string;
  prompt_version: string;
  scores: Record<string, number>;
  rationale: string;
  error: string | null;
  error_detail: string | null;
};

/** The run-v1 document verbatim; only the embedded agent report stays free-form. */
export type RunV1Dto = {
  schema_version: "run-v1";
  campaign_id: string;
  task_id: string;
  category: string;
  repeat: number;
  provider: string;
  agent_commit: string | null;
  model_identity: {
    name: string | null;
    context_window: number | null;
    max_output_tokens: number | null;
    stream: boolean | null;
  } | null;
  outcome: string;
  strict_success: boolean;
  artifact_correct: boolean;
  state: string | null;
  stop_reason: string | null;
  error_kind: string | null;
  failure_stage: string | null;
  failure_kind: string | null;
  harness_detail: string | null;
  agent_exit_code: number | null;
  agent_timed_out: boolean;
  agent_argv_options: string[];
  hashes: Record<string, string | null>;
  model: {
    usage: {
      input_tokens: number | null;
      output_tokens: number | null;
      cache_creation_input_tokens: number | null;
      cache_read_input_tokens: number | null;
    };
    main_requests: number;
    compaction_requests: number;
    attempts: number;
    network_retries: number | null;
    usage_coverage: number | null;
  };
  tools: {
    proposed: number;
    executed: number;
    succeeded: number;
    failed: number;
    skipped: number;
    duplicate_calls: number | null;
    truncated: number | null;
    output_bytes: number | null;
  };
  compaction: {
    count: number;
    requests: number;
    above_target: boolean;
    input_tokens_before: number | null;
    input_tokens_after: number | null;
    estimated_summary_tokens: number | null;
    provider_summary_output_tokens: number | null;
    estimated_minus_provider_tokens: number | null;
  };
  oracle: {
    target: {
      passed: boolean | null;
      exit_code: number | null;
      duration_ms: number | null;
      errored: boolean;
    };
    regression: {
      passed: boolean | null;
      exit_code: number | null;
      duration_ms: number | null;
      errored: boolean;
    };
  };
  modifications: {
    files_added: number;
    files_modified: number;
    files_deleted: number;
    lines_added: number;
    lines_removed: number;
    out_of_scope_paths: string[];
    forbidden_paths_modified: string[];
    detected_workspace_escape: boolean;
  };
  durations: {
    workspace_prepare_ms: number;
    agent_process_ms: number;
    oracle_ms: number;
    total_ms: number;
    agent_monotonic_ms: number | null;
    retry_wait_monotonic_ms: number | null;
    tool_execution_monotonic_ms: number | null;
  };
  agent_report: Record<string, JsonValue>;
  started_at: string | null;
  finished_at: string | null;
};

/** One run's facts plus its judgement; null fields mark unreadable records. */
export type RunDetailDto = {
  campaign: string;
  task_id: string;
  repeat: number;
  run: RunV1Dto | null;
  run_note: string | null;
  judgement: JudgementDto | null;
  judgement_note: string | null;
};

/** The read surface the evaluations view consumes; the HTTP client implements it. */
export interface EvaluationReader {
  listCampaigns(): Promise<CampaignSummaryDto[]>;
  campaignDetail(campaign: string): Promise<CampaignDetailDto>;
  runDetail(
    campaign: string,
    taskId: string,
    repeat: number,
  ): Promise<RunDetailDto>;
}
