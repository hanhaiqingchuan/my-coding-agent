# Evaluation harness

This directory holds the public part of the quantitative evaluation described in
section 18 of `doc/项目设计方案.md`: the versioned JSON schemas, twelve self-authored
redistributable tasks (the four P0 tasks plus the expansion to the full section 18.4
matrix), and redacted example results. The harness code lives in
`src/coding_agent/evaluation/`.

The harness measures the shipped product. It launches `coding-agent run` as a
subprocess with a frozen argv, contains no second planning or agent loop, and never
imports the Agent Loop, the Run Coordinator, or any tool internals.

## Commands

```bash
# Validate a manifest and prove every task can be scored.
uv run --python 3.12 coding-agent-eval validate \
  --manifest evaluation/tasks/public/manifest.toml

# Show what a real campaign would do without calling the model.
uv run --python 3.12 coding-agent-eval run \
  --manifest evaluation/tasks/public/manifest.toml \
  --repeats 1 --serial --out /path/outside/this/repo --dry-run

# Run the campaign for real. Requires a config file and ANTHROPIC_API_KEY.
uv run --python 3.12 coding-agent-eval run \
  --manifest evaluation/tasks/public/manifest.toml \
  --config config.toml --repeats 1 --serial --out /path/outside/this/repo

# Run the campaign and score every finished run with the LLM judge.
uv run --python 3.12 coding-agent-eval run \
  --manifest evaluation/tasks/public/manifest.toml \
  --config config.toml --repeats 1 --serial --out /path/outside/this/repo --judge

# Aggregate a campaign's run records into summary.json, summary.csv and report.md.
uv run --python 3.12 coding-agent-eval summarize --input /path/outside/this/repo

# Print the campaign history index for a results root.
uv run --python 3.12 coding-agent-eval history --results /path/to/private/results
```

`--dry-run` prints the task count, the upper bound on main model requests, the
workspace root and the output location. It creates nothing and calls no model, so it
does not need the auto-approve acknowledgement.

`run` writes only the records it owns — `runs.jsonl` and one `run.json` per repeat — and
`summarize` owns the aggregates, so the two commands never compete for a file. `summarize`
writes `summary.json`, `summary.csv` and `report.md` into `<campaign>/reports/` unless `--out`
names another directory, and refuses to write any of them where one already exists rather than
rewriting a published result.

P0 always runs a campaign serially; `--serial` records that intent explicitly.
Results are written outside this repository by convention: raw campaign directories
contain workspaces and databases and are not part of the delivery.

## Directory layout

```text
evaluation/
├── README.md
├── schemas/
│   ├── manifest-v1.schema.json    # documentation schema for the TOML manifest
│   ├── run-v1.schema.json         # one immutable evaluation run
│   ├── summary-v1.schema.json     # one redacted campaign aggregate
│   └── judgement-v1.schema.json   # one LLM-judged fuzzy-metric record
├── tasks/public/
│   ├── manifest.toml
│   └── <task-id>/                 # twelve tasks: four categories, three each
│       ├── prompt.md              # copied outside the workspace, passed as --prompt-file
│       ├── baseline/              # read-only; every repeat is a fresh copy
│       ├── gold/                  # gold patch as a file overlay
│       ├── error/                 # wrong implementation the target oracle must reject
│       └── oracle/
│           ├── target.py          # the task's goal assertions
│           └── regression.py      # the pre-existing suite must still pass
└── examples/
    ├── run-v1.redacted.json
    └── summary-v1.json
```

`baseline/`, `gold/`, `error/` and `oracle/` are siblings on purpose. The manifest
validator rejects any task whose prompt, gold overlay, error overlay or oracle sits
inside the baseline, because only the baseline is ever copied into a workspace.

## The two documents

Two versioned documents exist, because one process cannot know both halves.

`run-report-v1` is written by the agent itself (`--report-out`). It is a read-only
projection of facts SQLite already owns: run state, stop reason, error kind, the model
identity taken from the run's own configuration snapshot, model requests and attempts
with per-component provider usage, tool statistics with hashed arguments, compaction
facts, and per-phase durations. The identity carries the model name, context window,
max output tokens and stream flag — never the credential and never the API endpoint.

`run-v1` is written by the evaluator. It embeds the agent report verbatim under
`agent_report` and adds only harness facts: task id, category and repeat,
`provider = "anthropic_messages"`, the agent commit, the config/task/prompt/tool-schema
hashes, the failure stage and kind, the workspace tree and diff hashes, the oracle
results, and `strict_success` / `artifact_correct`. The model identity is copied out of
the agent report rather than re-read from the harness's own configuration, because only
the agent process knows what actually served its requests. The evaluator never opens the
agent's database and never re-derives an agent fact.

A written `run-v1` document is the immutable record of that run. `summarize` re-reads
those documents exactly as written: it never recomputes `strict_success`, `artifact_correct`
or the `failure_stage` and `failure_kind` pair, and it never derives an oracle outcome from a
score flag. A record that carries no failure kind contributes none. Scoring happens once, when
the run finishes.

## The judge and judgement-v1

`coding-agent-eval run --judge` scores every finished run with an LLM judge after its
`run.json` is written. The judge is the shipped `AnthropicMessagesModel` adapter — the
same `ModelSettings` as the campaign's own configuration, one streaming request, no
tools — so the evaluation adds no second model client and no third-party evaluation
framework.

The judge reads only the run's own `run-v1` facts plus the run's final assistant
message, reduced to a fixed excerpt first. The excerpt never contains prompt text,
tool arguments, command output, transcripts, credentials or absolute paths: the final
assistant message is redacted before it enters the prompt, and everything else comes
from the already-redacted run document. The shipped agent report does not currently
export a final assistant message, so in today's campaigns that field is absent and
the judge scores communication from the recorded facts alone.

The judge must answer one fixed JSON object with three 1–5 scores and a rationale:

| score | meaning |
| --- | --- |
| `task_completion` | did the run achieve the task's goal, relative to the prompt and the deterministic oracle facts |
| `process_quality` | were the tool choices and their order sensible — read before write, recovery after failures, no redundant calls |
| `communication` | does the final assistant message report the work honestly and briefly, and state how it was verified |

One malformed answer is retried once. A second malformed answer — or a failing model
request — becomes a recorded `judge_error` with an `error_detail`; a judge error never
aborts the campaign. Each record is a versioned `judgement-v1` document written to
`runs/<task-id>/repeat-<n>/judgement.json`, never overwriting an existing record, and
carries the judge model identity and the judge prompt version so scores from different
prompt versions are not silently compared.

Fuzzy scores never enter `strict_success`: the capability denominator stays exactly
the five deterministic conditions of section 18.5. The summary reports them beside the
deterministic metrics as `judged_runs`, `judge_error_runs`, `judge_means` and
`judge_coverage` (judged runs over started runs). `summarize` aggregates judgements
from the same per-run files, so the two commands cannot disagree.

## Campaign history

`coding-agent-eval history --results <dir>` prints a read-only index of every campaign
directory under a results root — campaign id, window, task count, started/valid/strict
success runs, the model identity, and the judge aggregates when judgements exist. A
campaign directory is one holding a `runs.jsonl` or at least one `runs/*/*/run.json`
record. The scan never writes, moves or deletes anything under the root; a directory
whose records cannot be read is reported as a corrupt entry with a note instead of
being skipped silently. Campaigns are listed oldest first, by their first recorded
start time.

## Task manifest

Every path in a manifest is relative to the manifest file, must stay inside its
directory, and must live outside the baseline. Validation rejects an unknown
`schema_version`, a path escape, a missing baseline or oracle, an unknown or missing
field, a duplicate task id, an unknown category, an empty or out-of-bounds
`allowed_paths`, a `forbidden_paths` entry overlapping `allowed_paths`, a timeout
outside 1–3600 seconds, a malformed pinned digest, and an empty, prefix-shaped or
out-of-bounds command allowlist.

Each task also pins the inputs it ships. `baseline_tree_hash` is the sha256 over the
canonical JSON of the baseline's path-to-content-hash mapping, and `target_oracle_hash`
and `regression_oracle_hash` are the sha256 of each oracle file's bytes. Validation
recomputes all three from disk and refuses the manifest with a field-scoped error on any
mismatch, so a baseline or an oracle cannot be edited between campaigns while results
still claim to come from the pinned task. The tree hash ignores `__pycache__`, `.git`,
`.DS_Store` and `*.pyc`, so merely running a baseline's own test suite cannot break the
pin. Run time keeps computing `hashes.task` independently; the manifest pin is what makes
that value comparable across campaigns.

`coding-agent-eval validate` additionally proves, by running the oracles,
that the baseline fails the target oracle, the baseline already passes its regression
oracle, the gold overlay passes both, and the error variant fails the target oracle.
Any of those failing is reported as `HARNESS_SETUP` for that task instead of an agent
failure.

## Oracle contract

An oracle is a stdlib-only Python script invoked as
`python -B <oracle> <candidate-workspace>` with a working directory outside that
workspace, an isolated `HOME`, and a minimal environment.

| Exit code | Meaning |
| --- | --- |
| `0` | passed |
| `1` | failed |
| anything else, or a timeout | `HARNESS_ORACLE_ERROR` |

Oracles never run inside the workspace, so the agent cannot influence its own grade,
and their stdout and stderr are discarded rather than exported.

## Campaign layout

```text
<out>/
├── runs.jsonl                   every run-v1 document, one per line, written by run
├── runs/<task-id>/repeat-<n>/   one directory per repeat, described below
├── setup/<task-id>/             the baseline, gold and error workspaces used to verify the task
└── reports/                     written by summarize, never by run
    ├── summary.json
    ├── summary.csv
    └── report.md
```

## Per-run isolation

Each repeat gets its own directory under `<out>/runs/<task-id>/repeat-<n>/`:

```text
workspace/              fresh copy of the read-only baseline
data/                   isolated --data-dir (its own SQLite database)
prompt.md               the task prompt, outside the workspace
command-policy.json     generated command-policy-v1 file, outside the workspace
canary.txt              guards against writes outside the workspace
oracle/                 oracle working directory
agent-report.json       the agent's run-report-v1 document
run.json                the evaluator's run-v1 document
judgement.json          the judge's judgement-v1 document, only with --judge
```

An existing campaign directory or run directory is never overwritten. The generated
command policy lists the manifest's exact command and cwd pairs; anything else the
model proposes returns a normal `COMMAND_NOT_ALLOWED` tool error that the model can
read and react to. This is contamination control, not a malicious-code sandbox.

## Success definition

```text
strict_success = target oracle passed
               + regression oracle passed
               + no forbidden path modified
               + no workspace escape detected
               + run finished as COMPLETED
```

`artifact_correct` reports the same conditions without the run-state requirement, so
a run whose code is right but which ended in `MAX_ROUNDS` is visible as
`artifact_correct_only` instead of being counted as a success.

`task_completion_rate = strict_success_runs / valid_runs`, where valid runs exclude
`HARNESS_SETUP` and `HARNESS_ORACLE_ERROR`. Both harness counts are still reported.
A task counts toward `robust_task_count` only when at least two of its three repeats
strictly succeed, and each task keeps its raw per-repeat booleans.

## Token usage and durations

Only provider usage is reported. `input_tokens`, `output_tokens`,
`cache_creation_input_tokens` and `cache_read_input_tokens` are kept separately, and a
component stays `null` whenever any request in scope lacks it — a local estimate is
never substituted, and streaming deltas are never summed. `usage_coverage` reports how
many finished requests carried provider usage.

Every harness duration comes from a monotonic clock. In the agent report,
`agent_monotonic_ms`, `retry_wait_monotonic_ms` and `tool_execution_monotonic_ms` are
monotonic; `model_request_elapsed_ms` and `compaction_request_elapsed_ms` are derived
from persisted request timestamps and are named accordingly.

The compaction estimate error compares this project's estimate of the summary it
produced with the provider's output token count for the request that produced it.
P0 does not persist a per-request context estimate, so no other estimate-versus-usage
figure is claimed.

## Redaction

Default exports contain no prompt text, no tool arguments, no command output, no
transcript and no absolute paths. Tool arguments appear only as `args_hash`, computed
over the canonically sorted JSON of the call input.

## About `examples/`

The two files in `examples/` were produced by a real offline campaign over the four
public tasks: the baselines, gold overlays, oracles, tree hashes, diff counts and
oracle durations are genuine. The agent process itself was replaced by a stub that
applies the gold overlay and emits a fixed `run-report-v1` document, so the model,
token and agent-duration values are placeholders that demonstrate the schema — they
are not measurements of a live model run.
