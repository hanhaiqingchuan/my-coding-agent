# My Coding Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在公开仓库 `my-coding-agent/` 中，从零实现一个可通过网页和 headless CLI 使用、支持多轮会话、三个本地工具、审批、恢复、上下文压缩及量化评测的 coding agent。

**Architecture:** 采用 FastAPI + React + SQLite 的事件驱动模块化单体。Agent Loop 只依赖模型、上下文、工具、审批和存储协议；SQLite 是唯一运行事实源，WebSocket 只广播已经提交的 durable event 和不落库的流式 delta。先通过 `ScriptedModel` 打通无网络的完整纵向链路，再接真实 Anthropic-compatible Messages API、网页界面和评测。

**Tech Stack:** Python 3.12、uv、FastAPI、Pydantic、官方 `anthropic` Python SDK、SQLite、pytest、Ruff、React、TypeScript、Vite、Vitest、Testing Library、Playwright。

**Spec:** `doc/项目设计方案.md`

## Global Constraints

- 以下所有相对路径均以公开仓库根目录为基准；`doc/` 只包含本设计方案和实施计划，私有评测素材、原始 transcript、录屏源文件和提交回执必须位于仓库外。
- Python 统一为 3.12；创建环境使用 `uv venv .venv --python 3.12`，安装依赖使用 `uv add`/`uv sync`，运行使用 `uv run --python 3.12`。
- 不使用任何 Agent 框架或 Agent SDK；官方 `anthropic` SDK 只负责 HTTP、认证、SSE 解码和基础反序列化，且设置 `max_retries=0`。
- 首版只支持 Anthropic-compatible Messages API，不实现 OpenAI Chat Completions、Responses API 或双协议 provider factory；Core 只保存厂商无关的有序 content parts。
- 模型可见工具固定为 `read_file`、`write_file(operation=write|replace)`、`run_command`；首版不增加 `complete`、`task_done`、`update_plan`、search 或 patch 工具。
- Run 的硬状态与 plan/execute/verify/reflect 等软工作方式分离；只有后端 Coordinator 可以改变运行状态。
- SQLite 是唯一权威状态；JSONL/CSV/Markdown 只作为导出或评测产物。
- 默认测试必须离线，使用临时 workspace、临时数据库、fake clock/sleeper、fake Anthropic Messages events 和 `ScriptedModel`；真实模型测试只在 `RUN_LIVE_TESTS=1` 时执行。
- 服务只监听 `127.0.0.1`。`run_command` 不是安全沙箱，写入和命令默认逐次审批；`--yes` 仍产生完整审计事件。
- API key 只从环境变量读取，不进入配置快照、日志、fixture、报告、README 或视频。
- 绝不执行 `rm`、`git clean` 等物理删除。需要移除文件时先征得用户同意，再移动到仓库外私有工作区的回收目录。
- 每个任务完成后先检查 diff 和测试证据；非破坏性的本地暂存、commit、分支和 worktree 操作已预授权，可按检查点自主执行。commit message 只能描述项目变更，禁止 AI/模型辅助署名、生成来源及 `Co-authored-by`/coauthor trailer。任何 push 或其它远端写操作仍须逐次取得用户明确授权。

## Milestones

1. **M1 · 可测试核心：** Task 1–3，配置、领域模型、SQLite 和事件不变量成立。
2. **M2 · Headless Agent：** Task 4–12，三工具、模型适配、**同步上下文压缩**、Agent Loop 和正式 headless CLI 可由 `ScriptedModel` 端到端驱动。
3. **M3 · Web 演示产品：** Task 12–15，REST/WebSocket、工作台、审批、停止与恢复可演示。
4. **M4 · P0 评测与交付：** Task 16 的主链/Stop E2E、Task 17 的 4 个公开任务各 1 次 smoke，以及 Task 18 的文档、安全审计、视频、公开仓库和表单提交完成。

P0 必须在 P1 前形成随时可提交的完整交付物。P1 才扩展 `pause_turn` continuation、非流式 Messages、P0 正确性边界之外的恢复矩阵/dirfd 安全加固、完整 12×3 campaign、压缩消融、SWE-bench 和进一步视觉优化；P1 失败不得延迟 P0 提交。

## Target File Map

```text
my-coding-agent/
├── Makefile                          # 统一开发、测试和启动入口
├── pyproject.toml / uv.lock          # Python 3.12 项目与锁定依赖
├── config.example.toml               # 可公开配置模板，只保存 key 的环境变量名
├── doc/                              # 可公开的设计方案与实施计划
├── src/coding_agent/
│   ├── core/                         # 领域类型、状态、不变量与取消
│   ├── storage/                      # SQLite schema、事务、恢复与导出
│   ├── model/                        # 内部模型协议、Messages SSE 聚合、重试和 Anthropic adapter
│   ├── context/                      # model view、预算、裁剪和同步压缩
│   ├── tools/                        # 路径边界与三个本地工具
│   ├── runtime/                      # approval、Agent Loop、Coordinator、指标
│   ├── api/                          # FastAPI REST/WebSocket 薄适配层
│   ├── evaluation/                   # headless campaign、评分和报告
│   └── prompts/                      # system 与 compaction prompts
├── tests/                            # 离线 unit/integration/evaluation 与测试支持
├── web/                              # React/Vite 工作台、组件测试和 Playwright 配置
├── e2e/                              # 浏览器端到端场景
├── evaluation/                       # 公开 schema、任务和脱敏示例
├── scripts/                          # 发布前安全与交付检查
├── README.md
└── README.txt
```

---

### Task 1: 工程骨架、配置与统一命令

**Files:**

- Create: `pyproject.toml`
- Create: `uv.lock`
- Create: `config.example.toml`
- Create: `Makefile`
- Create: `src/coding_agent/__init__.py`
- Create: `src/coding_agent/config.py`
- Create: `tests/conftest.py`
- Create: `tests/unit/test_config.py`
- Create: `web/package.json`
- Create: `web/package-lock.json`
- Create: `web/tsconfig.json`
- Create: `web/vite.config.ts`
- Create: `web/index.html`
- Create: `web/src/main.tsx`
- Create: `web/src/App.tsx`
- Modify: `.gitignore`

**Interfaces:**

- Produces: `AppSettings` 及嵌套的 `ServerSettings`、`ModelSettings`、`AgentSettings`、`ContextSettings`、`RetrySettings`、`ToolSettings`。
- Produces: `load_settings(config_path: Path | None, cli_overrides: Mapping[str, object], environ: Mapping[str, str]) -> AppSettings`。
- Produces: `resolve_api_key(settings: AppSettings, environ: Mapping[str, str]) -> str`；返回值不得进入 `repr` 或配置快照。

配置模型在本任务冻结且拒绝未知字段：`ServerSettings(port=8000, open_browser=true)`；`ModelSettings(base_url, model, api_key_env="ANTHROPIC_API_KEY", context_window, max_output_tokens, stream=true)`；`AgentSettings(max_rounds=30, tool_argument_retries=2, doom_loop_threshold=3)`；`ContextSettings(compact_trigger_ratio=0.80, compact_target_ratio=0.60, safety_margin_tokens=2048, summary_max_tokens=2048, recent_turns_min=2, recent_budget_ratio=0.40)`；`RetrySettings(max_attempts=5, initial_delay_seconds=2, max_delay_seconds=30, jitter_ratio=0.25)`；`ToolSettings(read_max_lines=800, read_max_bytes=40960, command_timeout_seconds=120, command_output_bytes=40960, kill_grace_seconds=3, pass_env=())`。

- [ ] **Step 1: 建立 Python 3.12 和前端工程元数据**

先用 `apply_patch` 创建唯一指向 `src/coding_agent/` 的 `pyproject.toml`，不要运行会生成 `src/my_coding_agent/` 和提前生成 README 的 `uv init --name my-coding-agent --package`。随后显式创建 Python 3.12 环境并使用 uv 添加依赖：

```bash
uv venv .venv --python 3.12
uv add fastapi "uvicorn[standard]" "anthropic==1.1.0"
uv add --dev pytest pytest-asyncio httpx ruff
npm --prefix web install react react-dom
npm --prefix web install --save-dev typescript vite @vitejs/plugin-react vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event @playwright/test eslint @eslint/js typescript-eslint prettier @types/react @types/react-dom
```

`pyproject.toml` 声明 `requires-python = ">=3.12,<3.13"`，并注册 `coding-agent` 与 `coding-agent-eval` 两个 console scripts。创建前端 `package.json` 后用 npm 安装 React/React DOM，以及 Vite、TypeScript、`@vitejs/plugin-react`、Vitest、jsdom、Testing Library、Playwright、ESLint、Prettier 等开发依赖并生成锁文件。

`package.json` 从本任务起冻结 scripts：`dev="vite"`、`build="tsc --noEmit && vite build"`、`test="vitest run"`、`test:e2e="playwright test -c playwright.config.ts"`、`lint="eslint ."`、`format="prettier --write ."`。`tests/conftest.py` 注册 `live` marker；未设置 `RUN_LIVE_TESTS=1` 时 collection hook 必须 skip 所有 live tests，确保 `make test/check` 不访问模型网络。当前内层仓库已经初始化并有 `.gitignore`，本任务先核对并补齐 secrets、SQLite、node_modules、dist 和测试产物规则，不重复 `git init`。

- [ ] **Step 2: 先写配置失败测试**

在 `tests/unit/test_config.py` 固定以下行为：

```python
def test_cli_override_wins_over_toml(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('[agent]\nmax_rounds = 7\n', encoding="utf-8")
    settings = load_settings(config_file, {"agent.max_rounds": 9}, {})
    assert settings.agent.max_rounds == 9

def test_secret_value_is_absent_from_settings_repr(valid_settings):
    key = resolve_api_key(valid_settings, {"ANTHROPIC_API_KEY": "secret-sentinel"})
    assert key == "secret-sentinel"
    assert "secret-sentinel" not in repr(valid_settings)
```

- [ ] **Step 3: 运行配置测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/unit/test_config.py -v
```

Expected: 因 `coding_agent.config` 或 `load_settings` 尚不存在而失败。

- [ ] **Step 4: 实现配置解析和校验**

使用标准库 `tomllib` 读取 TOML，按 `CLI > config.toml > defaults` 合并。实现字段级错误，覆盖缺少模型名/base URL/API-key 环境变量、窗口小于输出预算、P0 配置 `stream=false`、非法压缩比例、`max_rounds < 2` 和非正超时；错误只能包含环境变量名，不能包含值。

`ModelSettings` 固定为 `base_url/model/api_key_env/context_window/max_output_tokens/stream`；默认 key 名为 `ANTHROPIC_API_KEY`，默认 API 根为 `https://api.anthropic.com`。不得保留 `max_tokens_field`、`stream_usage` 或任意 beta/header 透传字段；`max_output_tokens` 始终映射为 Messages API 必填 `max_tokens`。

- [ ] **Step 5: 实现 Makefile 和最小前端启动页**

Makefile 实现设计稿第 15.1 节的 `help/install/build/start/dev-api/dev-web/test-backend/test-frontend/test/test-e2e/lint/format/check`。`test-backend` 固定执行 `uv run --python 3.12 pytest --ignore=tests/live`，自动发现当时已经存在的测试目录；live marker hook 作为第二道保护。`make install` 显式创建 Python 3.12 venv、按锁文件安装全部组、执行 `npm --prefix web ci` 和 `npm --prefix web exec -- playwright install chromium`；不得提供 `clean`/`distclean`。

- [ ] **Step 6: 验证骨架**

```bash
uv run --python 3.12 pytest tests/unit/test_config.py -v
uv run --python 3.12 ruff check src tests
npm --prefix web run build
make help
```

Expected: 全部命令退出码为 0；Vite 页面能编译，但此时不要求连接后端。

- [ ] **Step 7: 提交检查点（本地 commit 已预授权）**

建议提交信息：`chore: bootstrap project tooling and configuration`。

---

### Task 2: 领域模型、状态机与取消令牌

**Files:**

- Create: `src/coding_agent/core/models.py`
- Create: `src/coding_agent/core/events.py`
- Create: `src/coding_agent/core/errors.py`
- Create: `src/coding_agent/core/cancellation.py`
- Create: `tests/unit/test_models.py`
- Create: `tests/unit/test_state_machine.py`
- Create: `tests/unit/test_cancellation.py`

**Interfaces:**

```text
validate_transition(current: RunState, target: RunState) -> None
terminal_state_for(reason: StopReason) -> RunState
CancellationToken.cancel() -> None
CancellationToken.cancelled -> bool
CancellationToken.raise_if_cancelled() -> None
CancellationToken.wait() -> Awaitable[None]
```

`models.py` 定义 `RunState`（含 `CANCELLING`）、`StopReason`、`ErrorKind`、`MessageStatus`、`ApprovalStatus`、`ApprovalDecision`、`ApprovalRecord`、`ToolExecutionState`、`EffectStartResult`、`ModelStopReason`、`Usage`、`TextPart`、`ToolUsePart`、`ToolCall`、`ToolResult`、`AssistantTurn`、`Message`、`PreparedToolCall`、`PendingToolGroup`、`ContextSnapshot`、`SessionSnapshot`、`Session`、`ClientCommandRecord`、`Run` 和 `DurableEvent`。`AssistantTurn.parts` 保存厂商无关、有序的 `TextPart | ToolUsePart`，不能只拆成独立 text 与 tool-call 数组而丢失相对顺序。所有跨层 DTO 使用不可变 dataclass 或 Pydantic model，并禁止任意额外字段。

- [ ] **Step 1: 写状态转换和数据不变量测试**

```python
def test_terminal_state_cannot_transition():
    with pytest.raises(InvalidStateTransition):
        validate_transition(RunState.COMPLETED, RunState.MODEL_STREAMING)

@pytest.mark.parametrize(
    ("reason", "state"),
    [
        (StopReason.COMPLETED, RunState.COMPLETED),
        (StopReason.USER_STOP, RunState.CANCELLED),
        (StopReason.MAX_ROUNDS, RunState.STOPPED),
        (StopReason.MODEL_REFUSAL, RunState.STOPPED),
        (StopReason.PAUSE_TURN, RunState.STOPPED),
        (StopReason.SERVER_RESTART, RunState.INTERRUPTED),
        (StopReason.RETRY_EXHAUSTED, RunState.FAILED),
    ],
)
def test_stop_reason_maps_to_terminal_state(reason, state):
    assert terminal_state_for(reason) is state
```

另测成功 `ToolResult` 必须满足 `ok=true,error=null`，失败结果必须有稳定错误码，以及 assistant/tool-call ID 唯一性。

- [ ] **Step 2: 运行测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/unit/test_models.py tests/unit/test_state_machine.py tests/unit/test_cancellation.py -v
```

Expected: 因核心类型尚未实现而失败。

- [ ] **Step 3: 实现最小领域层**

状态转换表显式列出主路径、所有活动状态到 `CANCELLING`、`CANCELLING → CANCELLED`，以及各 stop reason 可到达的唯一终态；所有终态不可迁出。`MODEL_PROTOCOL_ERROR/AUTH_ERROR/CONFIG_ERROR/RETRY_EXHAUSTED/CONTEXT_OVERFLOW → FAILED`，`MAX_ROUNDS/DOOM_LOOP/EMPTY_RESPONSE/OUTPUT_TRUNCATED/INCOMPLETE_TOOL_CALL/MODEL_REFUSAL/PAUSE_TURN → STOPPED`。CancellationToken 使用 `asyncio.Event`，不依赖 FastAPI、SQLite 或 Anthropic SDK。定义内部 `RunOutcome` 的 `complete/stop/cancel/fail` 变体，禁止用自然语言决定终态。

- [ ] **Step 4: 验证领域层**

```bash
uv run --python 3.12 pytest tests/unit/test_models.py tests/unit/test_state_machine.py tests/unit/test_cancellation.py -v
uv run --python 3.12 ruff check src/coding_agent/core tests/unit/test_models.py tests/unit/test_state_machine.py tests/unit/test_cancellation.py
```

Expected: 全部通过。

- [ ] **Step 5: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: define run lifecycle and core contracts`。

---

### Task 3: SQLite 事实源、事务与启动恢复

**Files:**

- Create: `src/coding_agent/storage/__init__.py`
- Create: `src/coding_agent/storage/schema.sql`
- Create: `src/coding_agent/storage/sqlite.py`
- Create: `tests/unit/test_sqlite_store.py`
- Create: `tests/integration/test_store_recovery.py`

**Interfaces:**

```text
SQLiteStore.initialize() -> None
SQLiteStore.create_session(workspace_realpath: str, title: str | None) -> Session
SQLiteStore.list_sessions() -> list[Session]
SQLiteStore.begin_run(session_id: str, content: str, config_snapshot: Mapping[str, object], client_command_id: str, payload_hash: str) -> Run
SQLiteStore.load_committed_transcript(session_id: str) -> list[Message]
SQLiteStore.load_snapshot(session_id: str) -> SessionSnapshot
SQLiteStore.stage_tool_group(run_id: str, turn: AssistantTurn) -> PendingToolGroup
SQLiteStore.settle_tool_group(group_id: str, results: Sequence[ToolResult]) -> None
SQLiteStore.commit_final_turn(run_id: str, turn: AssistantTurn) -> None
SQLiteStore.request_cancellation(run_id: str, client_command_id: str, payload_hash: str) -> Run
SQLiteStore.resolve_approval(run_id: str, tool_call_id: str, decision: ApprovalDecision, client_command_id: str, payload_hash: str) -> ApprovalRecord
SQLiteStore.acknowledge_recovery(session_id: str, client_command_id: str, payload_hash: str) -> Session
SQLiteStore.begin_effect(run_id: str, tool_call_id: str) -> EffectStartResult
SQLiteStore.transition_run(run_id: str, expected: Collection[RunState], target: RunState, stop_reason: StopReason | None, error_kind: ErrorKind | None) -> Run
SQLiteStore.events_after(session_id: str, seq: int) -> list[DurableEvent]
SQLiteStore.recover_interrupted_runs() -> list[str]
```

- [ ] **Step 1: 写 schema 与事务失败测试**

测试数据库启动后启用 foreign keys、WAL 和 busy timeout；创建设计稿规定的八张表并设置 `PRAGMA user_version = 1`。用测试注入的异常证明 `begin_run`、`settle_tool_group` 和状态变更连同 durable event 要么全部提交，要么全部回滚。

- [ ] **Step 2: 写历史配对、去重和恢复测试**

```python
def test_pending_tool_group_is_excluded_from_model_history(store, session):
    run = store.begin_run(session.id, "change it", {}, "cmd-start", "hash-start")
    store.stage_tool_group(run.id, assistant_turn_with_two_calls())
    assert [m.role for m in store.load_committed_transcript(session.id)] == ["user"]

def test_duplicate_command_id_returns_original_result(store, session):
    first = store.begin_run(session.id, "task", {}, "cmd-1", "hash-a")
    second = store.begin_run(session.id, "task", {}, "cmd-1", "hash-a")
    assert second.id == first.id
```

另测同一 `client_command_id` 配不同 payload hash 返回 `COMMAND_ID_CONFLICT`；command receipt、领域变更和 ACK/event 同事务提交；注入 receipt 处理中的崩溃后重发不会吞命令；event `seq` 在 Session 内单调递增；snapshot 投影与 `snapshot_seq` 来自同一 read transaction。恢复时只要存在 `effect_started_at` 或 `unknown` 调用，就在同一事务设置 `sessions.requires_recovery_ack=1` 并写 durable event；重复 `acknowledge_recovery` 幂等，未确认前 `begin_run` 必须拒绝。

针对 `begin_effect` 写 CAS 测试：只有 Run 未请求取消、审批仍有效且调用尚未开始时才能写入 `effect_started_at`；与 `request_cancellation` 并发时只能有一方先成功，输的一方必须根据最新状态返回而非覆盖。

- [ ] **Step 3: 运行测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/unit/test_sqlite_store.py tests/integration/test_store_recovery.py -v
```

- [ ] **Step 4: 实现 schema 和 SQLiteStore**

使用标准库 `sqlite3`、短事务和显式 SQL，不引入 ORM。含 tool-use parts 的 assistant payload 必须保留 block 顺序；`pending_tools` 只有在所有 tool use 都已有恰好一个内部 tool result 后才能转为 `committed`。恢复时：遗留活动 run 变为 `INTERRUPTED/SERVER_RESTART`；正在运行的工具标 `unknown`，等待审批的工具标 `cancelled`，后续 queued 标 `skipped`，随后补齐并提交消息组；存在未知副作用时同时设置 recovery ack gate。

- [ ] **Step 5: 验证存储层**

```bash
uv run --python 3.12 pytest tests/unit/test_sqlite_store.py tests/integration/test_store_recovery.py -v
uv run --python 3.12 pytest tests/unit -q
```

Expected: 全部通过；测试数据库全部位于 pytest 临时目录。

- [ ] **Step 6: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: add transactional session store and recovery`。

---

### Task 4: Workspace 路径边界与 read_file

**Files:**

- Create: `src/coding_agent/tools/__init__.py`
- Create: `src/coding_agent/tools/paths.py`
- Create: `src/coding_agent/tools/read_file.py`
- Create: `src/coding_agent/tools/registry.py`
- Create: `tests/unit/test_workspace_paths.py`
- Create: `tests/unit/test_read_file.py`
- Create: `tests/unit/test_tool_registry.py`

**Interfaces:**

```text
WorkspaceBoundary(root: Path)
WorkspaceBoundary.resolve(path: str, allow_missing_leaf: bool = False) -> Path
ReadFileTool.prepare(call: ToolCall, workspace: WorkspaceBoundary) -> PreparedToolCall
ReadFileTool.execute(prepared: PreparedToolCall, context: ToolContext) -> Awaitable[ToolResult]
ToolRegistry.schemas() -> list[dict[str, object]]
ToolRegistry.prepare(call: ToolCall, workspace: WorkspaceBoundary) -> PreparedToolCall | ToolResult
ToolContext(workspace: WorkspaceBoundary, cancellation: CancellationToken, emit_output: OutputSink)
```

- [ ] **Step 1: 写路径逃逸测试**

覆盖根内相对路径、根内绝对路径、`..`、根外绝对路径、指向根外的 symlink、不存在文件的最近存在父目录，以及 Session workspace 创建后不可改变。P0 明确按非对抗性本地文件系统设计：每次实际 I/O 前重新 resolve/校验，但不宣称抵抗并发目录项替换；dirfd + `openat/O_NOFOLLOW` 强加固属于 P1。

- [ ] **Step 2: 写 read_file 契约测试**

```python
def test_read_file_stops_at_first_limit(tmp_workspace):
    result = execute_read(tmp_workspace, lines=900, bytes_per_line=100)
    assert result.ok is True
    assert result.truncated is True
    assert result.data["start_line"] == 1
    assert result.data["next_offset"] is not None
    assert len(result.data["content"].encode("utf-8")) <= 40960
```

另测 1-based offset、最多 800 行、目录、FIFO/socket/device 等非 regular file、二进制、非法 UTF-8、越界和未知工具。三个 JSON Schema 均设置 `additionalProperties=false`。

- [ ] **Step 3: 运行测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/unit/test_workspace_paths.py tests/unit/test_read_file.py tests/unit/test_tool_registry.py -v
```

- [ ] **Step 4: 实现路径边界、registry 和 read_file**

所有工具只能通过 `WorkspaceBoundary` 取得解析后的路径。错误统一返回 `ToolResult`，不能把用户可修正的路径或编码错误抛成 Run 级异常。

- [ ] **Step 5: 验证工具读取边界**

```bash
uv run --python 3.12 pytest tests/unit/test_workspace_paths.py tests/unit/test_read_file.py tests/unit/test_tool_registry.py -v
```

Expected: 全部通过，且测试不读取 fixture workspace 之外的文件。

- [ ] **Step 6: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: add workspace boundary and read tool`。

---

### Task 5: write_file 的预览、审批冻结与原子写

**Files:**

- Create: `src/coding_agent/tools/write_file.py`
- Create: `tests/unit/test_write_file.py`
- Modify: `src/coding_agent/tools/registry.py`
- Modify: `src/coding_agent/core/models.py`

**Interfaces:**

```text
WriteFileTool.prepare(call: ToolCall, workspace: WorkspaceBoundary) -> PreparedToolCall
WriteFileTool.execute(prepared: PreparedToolCall, context: ToolContext) -> Awaitable[ToolResult]
PreparedToolCall.preview -> UnifiedDiffPreview
PreparedToolCall.baseline_sha256 -> str | None
```

- [ ] **Step 1: 写 write 与 replace 失败测试**

覆盖：创建文件、空内容、整体覆盖、唯一 replace、零命中、多次命中、`replace_all=true`、空 `old_text`、父目录不存在、路径逃逸和 symlink 逃逸。

- [ ] **Step 2: 写 TOCTOU 与原子性测试**

```python
async def test_write_rejects_changed_baseline(prepared_write, target):
    target.write_text("external change", encoding="utf-8")
    result = await WriteFileTool().execute(prepared_write, tool_context())
    assert result.ok is False
    assert result.error.code == "WRITE_CONFLICT"
```

另用 monkeypatch 让最终 replace 失败，断言原文件内容保持不变；覆盖时保留原文件权限。

- [ ] **Step 3: 运行测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/unit/test_write_file.py -v
```

- [ ] **Step 4: 实现冻结提案与写入**

`prepare` 解析并冻结 canonical 参数、生成 unified diff、记录目标不存在或 SHA-256 baseline；`execute` 重新解析路径并比较 baseline，在同目录创建临时文件、复制权限、flush/fsync 文件后以原子替换提交，并在 POSIX 支持时 fsync 父目录。不得自动创建父目录。

- [ ] **Step 5: 验证写工具**

```bash
uv run --python 3.12 pytest tests/unit/test_write_file.py tests/unit/test_workspace_paths.py -v
```

- [ ] **Step 6: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: implement approved atomic file writes`。

---

### Task 6: run_command 输出、超时与进程组取消

**Files:**

- Create: `src/coding_agent/tools/run_command.py`
- Create: `tests/unit/test_run_command.py`
- Modify: `src/coding_agent/tools/registry.py`
- Modify: `src/coding_agent/core/cancellation.py`

**Interfaces:**

```text
RunCommandTool.prepare(call: ToolCall, workspace: WorkspaceBoundary) -> PreparedToolCall
RunCommandTool.execute(prepared: PreparedToolCall, context: ToolContext) -> Awaitable[ToolResult]
ToolContext.emit_output(text: str) -> Awaitable[None]
AllowedCommand(command: str, workspace_relative_cwd: str)
CommandPolicy(schema_version: "command-policy-v1", allowed: Sequence[AllowedCommand])
CommandPolicy.allows(command: str, canonical_cwd: Path) -> bool
```

- [ ] **Step 1: 写真实子进程测试**

覆盖 cwd 默认值与根内子目录、根外 cwd 拒绝、stdout/stderr 合并、exit code、40 KiB 统一截断、原始字节计数、超时、取消和子进程组退出。测试命令只使用当前 POSIX 环境中可用的 shell 内建或 `uv run --python 3.12 -c`。

- [ ] **Step 2: 写环境脱敏测试**

向父测试进程注入假的配置 key、常见凭据和一个未知名称 sentinel secret；子进程只报告变量是否存在，断言均为 false。默认 child env 只允许 `PATH`、locale、临时目录和隔离 HOME；其它变量必须通过未入库配置显式 allowlist，配置指定的模型 key 永远不可放行。禁止在失败输出中打印假值。

- [ ] **Step 3: 运行测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/unit/test_run_command.py -v
```

- [ ] **Step 4: 实现 POSIX 命令执行器**

使用 `asyncio.create_subprocess_shell` 并传入 `start_new_session=True`、`stderr=STDOUT`；持续读取直到进程退出，但 UI、数据库和模型只保留同一份最多 40 KiB 的缓冲。超时或取消先向进程组发 TERM，等待配置的 3 秒后仍存活再发 KILL。该保证是 process-group best effort，主动 `setsid`/daemonize 的后代不在 P0 保证范围。普通网页运行不设置 `CommandPolicy`；headless evaluation 使用 command 与 canonical cwd 双字段精确匹配，未命中返回 `COMMAND_NOT_ALLOWED`。不要支持 PTY、stdin 或后台 job。

- [ ] **Step 5: 验证命令工具并检查残留进程**

```bash
uv run --python 3.12 pytest tests/unit/test_run_command.py -v
uv run --python 3.12 pytest tests/unit/test_workspace_paths.py tests/unit/test_write_file.py tests/unit/test_run_command.py -q
```

Expected: 全部通过；测试启动且未主动脱离 process group 的子进程和孙进程均已退出。

- [ ] **Step 6: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: add bounded cancellable command execution`。

---

### Task 7: 模型协议、Messages SSE 聚合与 Anthropic Adapter

**Files:**

- Create: `src/coding_agent/model/__init__.py`
- Create: `src/coding_agent/model/protocol.py`
- Create: `src/coding_agent/model/message_assembler.py`
- Create: `src/coding_agent/model/anthropic_messages.py`
- Create: `tests/unit/test_message_assembler.py`
- Create: `tests/unit/test_anthropic_messages.py`
- Create: `tests/fixtures/anthropic_events.py`
- Create: `tests/fakes/__init__.py`
- Create: `tests/fakes/model.py`

**Interfaces:**

```text
ModelGateway.complete(request: ModelRequest, on_text_delta: DeltaSink, cancellation: CancellationToken) -> Awaitable[AssistantTurn]
MessageStreamAssembler.feed(event: NormalizedMessageEvent) -> Sequence[TextDelta]
MessageStreamAssembler.finish() -> AssistantTurn
AnthropicMessagesModel(settings: ModelSettings, api_key: str)
```

`protocol.py` 在本任务冻结 `ModelMessage(role: user|assistant, parts)`、`ModelRequest(system, messages, tools, max_tokens)`、`DeltaSink`、`Usage` 的 nullable 字段规则，以及三个异常：`ModelTransportError(retryable, cause)`、`ModelAPIError(status_code, error_type, retry_after, retryable)`、`ModelProtocolError(code, detail)`。`complete()` 成功只返回 `AssistantTurn`，失败只 raise 这些结构化异常。`NormalizedMessageEvent` 仅存在于 Anthropic adapter/assembler 边界，不能进入 Agent Core 或 SQLite。

- [ ] **Step 1: 写 Messages event assembler 失败测试**

固定以下事件矩阵：`message_start`；按 block index 到达的 `content_block_start/delta/stop`；`text_delta`；多个 `tool_use` blocks；`input_json_delta.partial_json` 多片拼接；`message_delta` 的 stop reason 与累计 usage；必要的 `message_stop`；可忽略的 `ping`；流内 `error`。断言 `input_tokens`、`output_tokens`、`cache_creation_input_tokens`、`cache_read_input_tokens` 取最终累计值、不按事件相加。另测事件乱序、重复/缺失 block、index/type 冲突、重复 tool-use ID、未知 block 类型、未闭合 input JSON、取消后晚到事件和缺少 `message_stop`。

```python
def test_tool_inputs_are_assembled_by_content_block_index():
    assembler = MessageStreamAssembler()
    for event in two_tool_use_event_stream():
        assembler.feed(event)
    turn = assembler.finish()
    assert [call.name for call in turn.tool_calls] == ["read_file", "run_command"]
    assert turn.tool_calls[0].arguments_raw == '{"path":"a.py"}'
```

- [ ] **Step 2: 运行聚合测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/unit/test_message_assembler.py -v
```

- [ ] **Step 3: 实现内部协议和纯聚合器**

模型层只输出有序 content parts 的 `AssistantTurn`；失败统一 raise 带稳定字段的 `ModelTransportError`、`ModelAPIError` 或 `ModelProtocolError`，不得把错误混入成功返回联合类型。聚合器按 block index 建槽，`tool_use` 的 ID/name 在 `content_block_start` 锁定，流完整结束后统一解析 `partial_json`。明确分流 `end_turn/tool_use/max_tokens/stop_sequence/pause_turn/refusal/model_context_window_exceeded`；完整合法的 tool-use blocks 在 `tool_use/end_turn/stop_sequence` 下优先执行并记录不一致诊断，任何 `max_tokens` + tool use 都拒绝执行。非流式 Messages 归一化属于 P1。

- [ ] **Step 4: 写 Adapter 请求映射测试**

mock `AsyncAnthropic`，断言构造时 `max_retries=0` 且 base URL 不手工拼接 `/v1/messages`。请求只发送 `model`、必填 `max_tokens`、顶层 `system`、`messages`、`tools`、Anthropic 对象格式 `tool_choice` 和 `stream`；禁止发送 `n`、`max_completion_tokens`、`stream_options`、beta 或任意 header 透传。最后一轮和压缩请求同时省略 `tools/tool_choice`。

错误映射测试必须基于 SDK exception 的 status/type/body：结构化 context-too-large 映射为一次可压缩重建信号，其它 400 不重试；不得通过匹配人类可读 message 判断。

- [ ] **Step 5: 写 canonical history 到 Messages wire 的映射测试**

断言 system/developer 内容只进入顶层 `system`；wire messages 只有 user/assistant role；assistant 的 text/tool-use blocks 保持顺序；同批工具结果合并为紧随其后的 user message 中的 `tool_result` blocks，以 `tool_use_id` 配对，失败设置 `is_error=true`。相邻同 role 只在不破坏 tool-use/result 原子边界时合并。

- [ ] **Step 6: 实现 AnthropicMessagesModel**

Adapter 不访问 workspace、SQLite 或 FastAPI。取消令牌必须关闭当前流；异常只归一化，不在本层自动重试。所有 delta 先交给调用方显示，只有 `finish()` 成功返回的完整 turn 才允许持久化。

- [ ] **Step 7: 验证模型层**

```bash
uv run --python 3.12 pytest tests/unit/test_message_assembler.py tests/unit/test_anthropic_messages.py -v
```

同时实现可由队列脚本驱动的 `tests/fakes/model.py::ScriptedModel`，它只实现相同 `ModelGateway` 协议，供 Compactor、Agent Loop 和 E2E 注入；生产配置和 CLI 不得暴露该 fake。

- [ ] **Step 8: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: add Anthropic Messages model adapter`。

---

### Task 8: 可取消的模型请求重试

**Files:**

- Create: `src/coding_agent/model/retry.py`
- Create: `tests/unit/test_retry.py`
- Modify: `src/coding_agent/core/events.py`

**Interfaces:**

```text
RetryingInvoker.invoke(operation: AsyncOperation[T], cancellation: CancellationToken, on_retry: RetrySink) -> Awaitable[T]
RetryNotice(attempt: int, max_attempts: int, delay_seconds: float, reason: str)
```

`retry.py` 定义 `AsyncOperation = Callable[[], Awaitable[T]]` 和 `RetrySink = Callable[[RetryNotice], Awaitable[None]]`；classifier 只接受 Task 7 的 typed model exceptions，不读取任意异常文本。

- [ ] **Step 1: 写重试分类和时序测试**

使用 fake sleeper、fake monotonic clock 和固定随机源，覆盖 Anthropic SDK `APIConnectionError`、`APITimeoutError` 以及 HTTP 408、409、429、5xx（显式包含 529 `OverloadedError`）；401、403、配置错误、refusal 和模型协议错误必须只调用一次。测试 `x-should-retry` 优先级、`retry-after-ms`、数值/HTTP-date `Retry-After`、非法值和 60 秒上限。

```python
async def test_retry_after_header_wins(fake_operation, fake_sleeper):
    fake_operation.fail_once(status=429, retry_after="7")
    result = await invoker(fake_operation, fake_sleeper)
    assert result == "ok"
    assert fake_sleeper.delays == [7.0]
```

- [ ] **Step 2: 运行测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/unit/test_retry.py -v
```

- [ ] **Step 3: 实现唯一重试所有者**

`max_attempts=5` 包含首次请求；无 `Retry-After` 时从 2 秒开始指数退避、加入最多 25% jitter、单次不超过 30 秒。等待使用 cancellation-aware await，立即响应 Stop。每次重试通过 callback 产生包含 attempt 与 deadline 的 durable event 数据。

- [ ] **Step 4: 验证重试与 Adapter 边界**

```bash
uv run --python 3.12 pytest tests/unit/test_retry.py tests/unit/test_anthropic_messages.py -v
```

Expected: 全部通过；不存在真实等待和网络访问。

- [ ] **Step 5: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: implement visible cancellable model retries`。

---

### Task 9: Context Builder、预算与确定性裁剪

**Files:**

- Create: `src/coding_agent/context/__init__.py`
- Create: `src/coding_agent/context/estimator.py`
- Create: `src/coding_agent/context/builder.py`
- Create: `tests/unit/test_context_estimator.py`
- Create: `tests/unit/test_context_builder.py`

**Interfaces:**

```text
estimate_input_tokens(system: str, messages: Sequence[ModelMessage], tool_schemas: Sequence[Mapping[str, object]]) -> int
ContextBuilder.build(transcript: Sequence[Message], snapshot: ContextSnapshot | None, request: ContextRequest) -> ContextBuildResult
ContextBuildResult = ReadyContext | CompactionRequired | ContextOverflow
```

`ContextRequest` 包含 context window、输出预算、safety margin、trigger/target ratio、summary 上限、最近轮次数和 wire tool schemas；三个结果类型分别携带完整 view/估算、压缩候选完整组，或 required/available/mandatory-user token 诊断。

- [ ] **Step 1: 写预算与保真测试**

构造包含多轮 user、assistant 和 tool group 的 transcript，逐字节断言所有 user 原文、role 和顺序不变且不重复。测试只选择 committed 消息，排除 pending group，保留当前 run 已 committed 的未摘要工具往返和最近至少两个用户轮次；assistant tool call 与全部 tool results 不可拆分。

- [ ] **Step 2: 写阈值与裁剪测试**

```python
def test_mandatory_content_overflow_prevents_model_call(long_user_transcript):
    result = builder.build(long_user_transcript, None, small_window_request())
    assert isinstance(result, ContextOverflow)
    assert result.code == "CONTEXT_OVERFLOW"
    assert result.mandatory_user_tokens > result.available_tokens

def test_old_tool_output_is_pruned_before_summary(large_tool_transcript):
    result = builder.build(large_tool_transcript, None, constrained_request())
    assert result.pruned_bytes > 0
    assert "tool=" in serialize(result.view)
```

估算覆盖 UTF-8 `ceil(bytes/3)`、顶层 system、每条 message/content block 固定开销和 Anthropic `input_schema` wire schema；明确它只是启发式估算，并用高熵 ASCII/代码/转义 JSON 测试和 API usage 记录误差。达到 80% 返回压缩计划，60% 是不低于 mandatory floor 的软目标。

- [ ] **Step 3: 运行测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/unit/test_context_estimator.py tests/unit/test_context_builder.py -v
```

- [ ] **Step 4: 实现纯 Context Builder**

Builder 不修改数据库和 canonical transcript，只生成厂商无关投影或结构化 overflow。较早大 tool output 先替换为包含工具名、状态、目标、原始大小和截断事实的占位内容；被保留的 assistant tool-use 与内部 tool results 必须保持原子配对，最后由 Adapter 编译成合法的 Anthropic assistant/user content-block 序列。

- [ ] **Step 5: 验证上下文层**

```bash
uv run --python 3.12 pytest tests/unit/test_context_estimator.py tests/unit/test_context_builder.py -v
```

- [ ] **Step 6: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: build token-aware context projections`。

---

### Task 10: [P0] 同步 Compactor 与摘要快照

**Files:**

- Create: `src/coding_agent/context/compactor.py`
- Create: `src/coding_agent/prompts/system.md`
- Create: `src/coding_agent/prompts/compact.md`
- Create: `tests/unit/test_compactor.py`
- Create: `tests/integration/test_context_persistence.py`
- Modify: `src/coding_agent/storage/sqlite.py`

**Interfaces:**

```text
Compactor.compact(plan: CompactionPlan, cancellation: CancellationToken) -> Awaitable[CompactionResult]
CompactionResult(snapshot: ContextSnapshot | None, error: CompressionError | None)
```

`CompactionPlan` 包含 source event IDs、旧 snapshot、按完整组划分的 chunks、每个 chunk 的 input budget 和 summary max tokens；`CompressionError` 包含 phase、required/available tokens、retryable 和安全错误码，不能只存自由文本。

- [ ] **Step 1: 写摘要协议和失败回退测试**

测试摘要的可压缩对象只包含完整 assistant/tool 组及上一有效摘要；关联 user 原文可作为只读语境传入 summarizer，但不得成为被替换对象。覆盖空摘要、结构错误、超出摘要预算、模型错误和取消，断言 canonical transcript 与旧 snapshot 均不改变。

- [ ] **Step 2: 写替换与分块测试**

```python
async def test_new_snapshot_replaces_old_summary(compactor, old_snapshot):
    result = await compactor.compact(plan_with(old_snapshot), token())
    assert result.snapshot.version == old_snapshot.version + 1
    assert result.snapshot.source_event_ids == ("event-1", "event-2")
```

断言分块边界只位于完整组之间，snapshot 保存来源事件 ID、模型、估算器、token 数和版本；SQLite 中只有一个 current snapshot。压缩请求自身按 `context_window - summary_max_tokens - safety_margin - protocol_overhead` 分块；最终视图尽量达到 60%，mandatory floor 高于 60% 时允许 `compaction_above_target=true`。

- [ ] **Step 3: 运行测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/unit/test_compactor.py tests/integration/test_context_persistence.py -v
```

- [ ] **Step 4: 实现同步压缩**

使用省略 `tools/tool_choice` 的独立 Anthropic Messages 请求；system 指令放顶层 `system`，待摘要内容作为 user/assistant Messages 输入。以 JSON 转义序列化完整组，解析固定字段：已完成工作及证据、重要文件/符号、工具结论、命令与测试、失败尝试、未完成事项、阻塞和下一步。新 snapshot 通过 SQLite 事务替换旧版本。首版不启动异步压缩任务。

- [ ] **Step 5: 验证压缩与历史不变量**

```bash
uv run --python 3.12 pytest tests/unit/test_context_builder.py tests/unit/test_compactor.py tests/integration/test_context_persistence.py -v
```

- [ ] **Step 6: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: add loss-aware synchronous compaction`。

---

### Task 11: Approval Gate、Agent Loop 与 ScriptedModel 闭环

**Files:**

- Create: `src/coding_agent/runtime/__init__.py`
- Create: `src/coding_agent/runtime/approval.py`
- Create: `src/coding_agent/runtime/coordinator.py`
- Create: `src/coding_agent/runtime/publisher.py`
- Create: `src/coding_agent/runtime/loop.py`
- Create: `src/coding_agent/runtime/metrics.py`
- Create: `tests/fakes/tools.py`
- Create: `tests/unit/test_approval.py`
- Create: `tests/unit/test_event_publisher.py`
- Create: `tests/integration/test_agent_loop.py`
- Create: `tests/integration/test_coordinator.py`

**Interfaces:**

```text
ApprovalGate.request(prepared: PreparedToolCall, cancellation: CancellationToken) -> Awaitable[ApprovalDecision]
AgentLoop.run(run_id: str, session_id: str, cancellation: CancellationToken) -> Awaitable[RunOutcome]
RunMutationGate.begin_effect(run_id: str, tool_call_id: str) -> Awaitable[EffectStartResult]
RunMutationGate.request_stop(run_id: str, client_command_id: str) -> Awaitable[Run]
EventPublisher.session_guard(session_id: str) -> AsyncContextManager[None]
EventPublisher.subscribe_locked(session_id: str) -> EventSubscription
EventPublisher.publish_committed(event: DurableEvent) -> Awaitable[None]
EventPublisher.unsubscribe(subscription: EventSubscription) -> Awaitable[None]
ScriptedModel(script: Sequence[AssistantTurn | Exception])
```

- [ ] **Step 1: 写首条完整失败测试**

用 `ScriptedModel` 驱动以下序列，不调用网络：

```text
user → read_file → write_file(approve) → run_command(approve) → final assistant text
```

断言工具严格按原顺序执行；assistant/tool group 先 pending、全部结果齐全后 committed；最终 `COMPLETED/COMPLETED`；写入与命令各有 requested/resolved 审计事件。

- [ ] **Step 2: 写审批与工具批次测试**

覆盖 read 自动执行；`--yes` 自动批准但仍审计；Reject 当前为 `rejected`、剩余为 `skipped` 并回灌模型；Stop 时未取得 effect-start 的当前调用为 `cancelled`、后续 queued 为 `skipped`，已经开始的调用按真实结果结算；未知工具直接返回 `UNKNOWN_TOOL` 且不审批。

增加 `approve-first/stop-first × write/command` 四组并发测试。`begin_effect` 必须在同一 Coordinator 临界区内以条件事务检查 Run 尚未取消并写入 `effect_started_at/TOOL_RUNNING`；Stop 先进入 `CANCELLING` 时旧审批不得启动工具。effect 已开始后按真实结果记录，不能一律改写为 cancelled。

- [ ] **Step 3: 写循环终止测试**

```python
async def test_text_plus_tool_call_continues(loop_fixture):
    outcome = await loop_fixture.run(script=text_and_tool_then_final())
    assert outcome.state is RunState.COMPLETED
    assert loop_fixture.model.call_count == 2

async def test_repetition_guard_stops_fourth_proposal(loop_fixture):
    outcome = await loop_fixture.run(script=repeated_identical_calls(4))
    assert outcome.stop_reason is StopReason.DOOM_LOOP
    assert loop_fixture.tools.execution_count == 2
```

另测参数错误最多自纠两次、空回复一次语义重试、`max_tokens` 纯文本产生 `STOPPED/OUTPUT_TRUNCATED`、任何 `max_tokens` + tool use 都产生 `STOPPED/INCOMPLETE_TOOL_CALL` 且执行次数为 0、`refusal` 产生 `STOPPED/MODEL_REFUSAL`、P0 的 `pause_turn` 产生 `STOPPED/PAUSE_TURN`、`model_context_window_exceeded` 只触发一次压缩重建、`max_rounds` 最后一轮禁 tools，以及模型自然语言“已完成”不覆盖实际 tool-use blocks。

- [ ] **Step 4: 运行测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/unit/test_approval.py tests/integration/test_agent_loop.py -v
```

- [ ] **Step 5: 实现统一循环**

本任务先实现 Coordinator 的最小 `RunMutationGate`、全局活动 Run lock、effect/Stop 线性化和 `EventPublisher`。SQLite 事务成功提交后才能调用 `publish_committed`；publisher 的 session guard 同时保护 snapshot cut、subscribe 注册和 live publish，断开时必须 unsubscribe。为 publisher 提供内存 fake，测试提交失败不广播、订阅/注销、慢消费者隔离和 session 间不串流。循环依赖注入 `SQLiteStore`、`ContextBuilder`、`Compactor`、`ModelGateway`、`RetryingInvoker`、`ToolRegistry`、`ApprovalGate` 和 publisher，不 import FastAPI。P0 不实现 `pause_turn` continuation；`pause_turn`、`refusal` 和超限分别进入明确终态。plan/execute/verify/reflect 只由 system prompt 引导；工具或测试失败结果回灌下一轮，不新增认知阶段状态或完成工具。

每次主模型和压缩模型请求均建立 `model_requests` 记录，保存 `kind`、round、attempt、非敏感配置 hash、开始/结束时间、usage、重试次数与累计等待；流失败的 draft 只能作为 UI interrupted 记录，不能进入 canonical transcript。

- [ ] **Step 6: 验证 M2 核心闭环**

```bash
uv run --python 3.12 pytest tests/unit tests/integration/test_agent_loop.py tests/integration/test_coordinator.py -q
```

Expected: 全部通过，测试期间无网络访问。

- [ ] **Step 7: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: implement the auditable agent loop`。

---

### Task 12: Run Coordinator、停止竞态与 headless CLI

**Files:**

- Modify: `src/coding_agent/runtime/coordinator.py`
- Create: `src/coding_agent/main.py`
- Create: `src/coding_agent/cli.py`
- Modify: `tests/integration/test_coordinator.py`
- Create: `tests/integration/test_stop_paths.py`
- Create: `tests/integration/test_headless_run.py`

**Interfaces:**

```text
RunCoordinator.start_run(session_id: str, content: str, client_command_id: str) -> Awaitable[Run]
RunCoordinator.stop_run(run_id: str, client_command_id: str) -> Awaitable[Run]
RunCoordinator.resolve_approval(run_id: str, tool_call_id: str, decision: ApprovalDecision, client_command_id: str) -> Awaitable[None]
run_headless(workspace: Path, data_dir: Path, prompt_file: Path, report_out: Path, settings: AppSettings) -> Awaitable[int]
cli.main(argv: Sequence[str] | None = None, dependencies: RuntimeDependencies | None = None) -> int
```

`RuntimeDependencies` 是 `main.py` 中的 composition-root dataclass，显式持有 store、model gateway、context builder/compactor、tool registry、approval gate、clock/sleeper 和 event publisher；生产由配置构造，测试直接注入，任何模块不得读取隐藏全局单例。

- [ ] **Step 1: 写单活动 Run 和命令幂等测试**

两个 Session 同时调用 `start_run` 时只有第一个成功；重复相同 `client_command_id` 返回原资源，同 ID 不同 payload 返回 `COMMAND_ID_CONFLICT`。测试必须证明没有重复模型调用、审批或文件副作用。

- [ ] **Step 2: 写四个停止点的竞态测试**

分别在 `MODEL_STREAMING`、`RETRY_WAIT`、`AWAITING_APPROVAL` 和 `TOOL_RUNNING` 阻塞，再调用 Stop。断言 Stop 先持久化 `cancellation_requested_at/CANCELLING`，只有一个终态事务获胜；已接受 Stop 后，迟到的模型完成、批准或尚未开始的工具不能再产生副作用，也不能把 Run 改成 `COMPLETED`。已经取得 effect-start 的工具按实际完成、取消或 unknown 结果结算。

- [ ] **Step 3: 写 headless CLI 测试**

```python
def test_headless_run_uses_isolated_paths(task_fixture, scripted_dependencies):
    exit_code = main([
        "run", "--config", str(task_fixture.config),
        "--workspace", str(task_fixture.workspace),
        "--data-dir", str(task_fixture.data_dir),
        "--prompt-file", str(task_fixture.prompt),
        "--yes", "--ack-unsafe-auto-approve",
        "--command-policy", str(task_fixture.command_policy),
        "--report-out", str(task_fixture.report),
    ], dependencies=scripted_dependencies)
    assert exit_code == 0
    assert json.loads(task_fixture.report.read_text())["state"] == "COMPLETED"
```

CLI 使用标准库 `argparse`，`serve` 与 `run` 子命令都接受 `--config`；console entry point 调用同步 `main()`，内部用 `asyncio.run` 进入异步 composition root。CLI 测试通过显式 `RuntimeDependencies` 注入 ScriptedModel；产品参数中不得暴露选择 ScriptedModel 的选项。

P0 冻结以下安全语义：`run --yes` 必须同时提供 `--ack-unsafe-auto-approve` 和非空 `--command-policy PATH`，否则以 `CONFIG_ERROR` 在创建 Run 前退出；policy 是版本化 JSON，逐项包含完整 command 字符串和 workspace-relative cwd，不接受正则、shell 前缀或 glob。`serve --yes` 由操作者启动参数本身确认风险，但仍受浏览器 Host/Origin/CSRF 防护。增加 parser、空 policy、cwd 越界、命令不匹配和合法精确匹配测试。

- [ ] **Step 4: 实现 Coordinator 和 CLI**

Coordinator 是唯一运行所有者，用 async lock 保护全局活动 Run；状态变更和 durable event 先写 SQLite，再广播。Stop 先持久化 `CANCELLING`，再设置取消令牌，完成当前流/工具的清理与配对后写 `CANCELLED/USER_STOP`。`serve` 支持 `--config/--workspace/--data-dir/--port/--open/--yes`；`run` 要求显式 config、workspace、data-dir、prompt-file 和 report-out，并可接受 evaluator 专用的 `--command-policy`。

- [ ] **Step 5: 验证 headless 产品入口**

```bash
uv run --python 3.12 pytest tests/integration/test_coordinator.py tests/integration/test_stop_paths.py tests/integration/test_headless_run.py -v
```

- [ ] **Step 6: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: coordinate runs and expose headless execution`。

---

### Task 13: FastAPI REST、WebSocket 与快照协议

**Files:**

- Create: `src/coding_agent/api/__init__.py`
- Create: `src/coding_agent/api/app.py`
- Create: `src/coding_agent/api/dependencies.py`
- Create: `src/coding_agent/api/schemas.py`
- Create: `src/coding_agent/api/routes.py`
- Create: `src/coding_agent/api/websocket.py`
- Create: `tests/integration/test_api.py`
- Create: `tests/integration/test_websocket.py`

**Interfaces:**

```text
GET  /api/health
GET  /api/bootstrap
GET  /api/config/public
GET  /api/directories?path=<absolute-path>
POST /api/sessions
GET  /api/sessions
GET  /api/sessions/{session_id}/snapshot
WS   /api/ws
```

WebSocket 命令统一为：

```json
{"type":"run.start","client_command_id":"uuid","session_id":"uuid","payload":{"content":"Fix the test"}}
```

客户端命令类型固定为 `session.subscribe`、`run.start`、`run.stop`、`approval.resolve` 和 `session.ack_recovery`；每种命令都携带 `client_command_id` 和 `session_id`，停止与审批还必须携带后端已有的 `run_id`/`tool_call_id`。`requires_recovery_ack=true` 时只允许订阅和确认恢复风险，不接受新 Run。

服务端消息只允许 `ack`、`command_error`、`snapshot`、`DurableEvent`、`assistant.delta` 和 `tool.output.delta` 六类 envelope。Python Pydantic schema 与后续 TypeScript 类型必须使用相同字段名，并设置 `extra="forbid"`。

`SessionSnapshotDto` 必须包含 `snapshot_seq`。订阅握手在 session publisher lock 内完成 snapshot read transaction 与 subscriber 注册；前端应用 snapshot 后设置 `lastSeq=snapshot_seq`，服务端只补发更大的 seq，握手期间的新 live event 先缓冲。所有 delta 携带 `run_id` 和 draft epoch，重连后不得把旧 epoch 拼到新 draft。

`BootstrapDto` 包含当前进程 token 和公开 WS URL；token 不落库、不进日志、仅存浏览器内存。服务重启后旧 token 通过专用 WS close code/HTTP 403 失效，client 最多自动重新 bootstrap 一次后再 subscribe，避免认证失败无限重连。

- [ ] **Step 1: 写 REST 契约测试**

覆盖健康检查、bootstrap、脱敏公开配置、目录列举、创建/列出 Session、非法/不可访问目录、Session 固定 canonical workspace 和完整 snapshot。

- [ ] **Step 2: 写 WebSocket 命令测试**

```python
def test_duplicate_run_start_is_idempotent(test_client, session_id):
    command = run_start_command(session_id, command_id="same-id")
    first = send_ws_command(test_client, command)
    second = send_ws_command(test_client, command)
    assert second["resource_id"] == first["resource_id"]
    assert count_runs(session_id) == 1
```

另测同 ID 不同 payload 冲突、command receipt 后崩溃重放、非法 schema、不存在 Session、全局已有活动 Run、审批引用错误 Run/tool call、`requires_recovery_ack` 拒绝 start、幂等的 `session.ack_recovery`，以及 snapshot 读取期间并发提交 event 时不漏不重。`run.start/run.stop/approval.resolve/session.ack_recovery` 的 receipt、领域变更和 ACK/event 必须同事务；服务重启后的旧 token 必须被拒绝，新 bootstrap token 可成功恢复订阅。

- [ ] **Step 3: 运行测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/integration/test_api.py tests/integration/test_websocket.py -v
```

- [ ] **Step 4: 实现薄 API 层**

route/WS handler 只校验输入、调用 Coordinator/Store、返回 DTO；不得在 API 层实现 Agent 决策。浏览器断开只注销 subscriber，不取消 Run。没有订阅者时写入和命令仍停留在审批状态，绝不自动批准。

生产模式启用可信 Host 和同源 Origin 检查，只接受 `127.0.0.1`/`localhost` 的实际服务端口；开发模式仅额外允许显式配置的 loopback Vite origin。启动时生成随机 CSRF/session token，通过同源 bootstrap 提供，所有改变状态的 REST/WS 命令必须携带；`--yes` 不绕过 Host、Origin 或 token 校验。增加恶意 Origin、错误 Host、缺失/错误 token 的拒绝测试。

- [ ] **Step 5: 验证 API 与既有核心**

```bash
uv run --python 3.12 pytest tests/integration/test_api.py tests/integration/test_websocket.py tests/integration/test_agent_loop.py -v
```

- [ ] **Step 6: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: expose durable sessions over REST and WebSocket`。

---

### Task 14: React 状态层与 Session 外壳

**Files:**

- Create: `web/src/api/types.ts`
- Create: `web/src/api/schema.fixture.json`
- Create: `web/src/api/client.ts`
- Create: `web/src/api/socket.ts`
- Create: `web/src/features/sessions/sessionReducer.ts`
- Create: `web/src/features/sessions/useSession.ts`
- Create: `web/src/components/AppShell.tsx`
- Create: `web/src/components/SessionSidebar.tsx`
- Create: `web/src/components/WorkspacePicker.tsx`
- Create: `web/src/components/RunDetailsPanel.tsx`
- Create: `web/src/styles.css`
- Create: `web/src/features/sessions/sessionReducer.test.ts`
- Create: `web/src/components/AppShell.test.tsx`
- Modify: `web/src/App.tsx`

**Interfaces:**

`types.ts` 显式定义 `RunDto`、`MessageDto`、`ToolExecutionDto`、`PendingApprovalDto`、`SessionSnapshotDto(snapshot_seq, active_run, messages, tools, pending_approval, interrupted_banner)`，以及包含 `CANCELLING` 的完整 `RunState` 和完整 `StopReason` union。后端测试导出一份固定 JSON schema fixture，前端契约测试验证必填字段和 enum 一致，避免手写 DTO 静默漂移。

```typescript
type SessionViewState = {
  snapshot: SessionSnapshotDto | null
  draftText: string
  connection: "connecting" | "connected" | "reconnecting" | "offline"
  lastSeq: number
  csrfToken: string | null
}

function reduceServerMessage(state: SessionViewState, message: ServerMessage): SessionViewState
```

- [ ] **Step 1: 冻结 TypeScript DTO**

在 `types.ts` 定义与后端完全对应的 `RunState`、`StopReason`、`ApprovalDecision`、`ToolExecutionState`、`SessionSnapshotDto`、`ClientCommand`、`ServerMessage` 和 `DurableEvent`。不要在组件中复制字符串联合类型。

- [ ] **Step 2: 写 reducer 和连接失败测试**

```typescript
it("ignores an already-applied durable event", () => {
  const once = reduceServerMessage(initialState, eventWithSeq(4))
  const twice = reduceServerMessage(once, eventWithSeq(4))
  expect(twice).toEqual(once)
})
```

覆盖 snapshot 覆盖本地推测状态、durable event 按 seq 去重、delta 不推进 durable seq、断线保持 active Run、重连重新 subscribe，以及 Session 切换清理旧连接。

- [ ] **Step 3: 运行测试并确认红灯**

```bash
npm --prefix web run test -- sessionReducer.test.ts AppShell.test.tsx
```

- [ ] **Step 4: 实现 API client、socket 和三栏外壳**

桌面布局为左侧 Session/Workspace、中间对话、右侧 Run details；窄屏将右栏放入抽屉。状态 reducer 是 UI 的唯一事实入口；组件不能根据按钮点击自行宣布 Run 完成。API client 先从同源 bootstrap 获取短期 CSRF/session token，仅保存在内存并在所有状态变更请求和 WebSocket subscribe 中携带；收到认证 close code/403 时清除旧 token、最多重新 bootstrap 一次再订阅。

- [ ] **Step 5: 验证前端基础**

```bash
npm --prefix web run test -- sessionReducer.test.ts AppShell.test.tsx
npm --prefix web run build
```

- [ ] **Step 6: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: add session workbench shell and event state`。

---

### Task 15: 对话时间线、Send/Stop 与固定审批坞

**Files:**

- Create: `web/src/components/ConversationTimeline.tsx`
- Create: `web/src/components/MessageBubble.tsx`
- Create: `web/src/components/ToolCard.tsx`
- Create: `web/src/components/ApprovalDock.tsx`
- Create: `web/src/components/Composer.tsx`
- Create: `web/src/components/ConversationTimeline.test.tsx`
- Create: `web/src/components/ApprovalDock.test.tsx`
- Create: `web/src/components/Composer.test.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**

```typescript
type ComposerProps = {
  activeRun: RunDto | null
  draft: string
  onDraftChange(value: string): void
  onSend(content: string): void
  onStop(runId: string): void
}
```

- [ ] **Step 1: 写 Send/Stop 与草稿测试**

```typescript
it("replaces Send with Stop while a run is active", async () => {
  render(<Composer
    activeRun={runningRun}
    draft="next"
    onDraftChange={handlers.onDraftChange}
    onSend={handlers.onSend}
    onStop={handlers.onStop}
  />)
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull()
  await userEvent.click(screen.getByRole("button", { name: "Stop" }))
  expect(handlers.onStop).toHaveBeenCalledWith(runningRun.id)
})
```

覆盖所有可操作活动状态均显示 Stop、`CANCELLING` 显示禁用的“正在停止”、终态恢复 Send、运行期间可编辑但不能发送下一条消息、空输入不能启动 Run。

- [ ] **Step 2: 写审批坞和工具卡测试**

审批坞必须位于输入框正上方并脱离消息滚动容器；同一时刻只显示当前调用。Approve/Reject 只发送 call ID 与 decision，不回传或修改冻结参数。处理后卡片从 dock 消失，并在时间线保留历史 ToolCard。

- [ ] **Step 3: 写状态展示测试**

工具卡覆盖 queued、awaiting approval、running、succeeded、failed、rejected、cancelled、skipped、unknown；右栏展示模型、round、上下文占用、重试和 stop reason。普通 `INTERRUPTED/SERVER_RESTART` 显示历史横幅并回到 IDLE；`requires_recovery_ack=true` 时 Composer 禁止发送，横幅提供“我已检查 workspace/进程”确认按钮并发送 `session.ack_recovery`。

- [ ] **Step 4: 运行测试并确认红灯**

```bash
npm --prefix web run test -- Composer.test.tsx ApprovalDock.test.tsx ConversationTimeline.test.tsx
```

- [ ] **Step 5: 实现交互和视觉样式**

以设计方案第 13 节确认的 workbench 信息层级为视觉基线，实现克制的 Codex 风格间距、状态色、hover/focus 和窄屏响应式布局；私有视觉草稿不复制进公开仓库。使用语义化按钮、键盘焦点和 ARIA label。流式 draft 与 committed assistant 消息必须是不同 UI 数据，收到终态或重连 snapshot 时清理不合法的 stale draft。

- [ ] **Step 6: 验证 UI**

```bash
npm --prefix web run test
npm --prefix web run build
```

- [ ] **Step 7: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: build conversational UI and approval dock`。

---

### Task 16: 重连、服务重启与 Playwright E2E

**Files:**

- Create: `tests/support/scripted_server.py`
- Create: `tests/integration/test_restart_recovery.py`
- Create: `web/playwright.config.ts`
- Create: `e2e/agent-flow.spec.ts`
- Create: `e2e/stop-reconnect.spec.ts`
- Modify: `src/coding_agent/api/app.py`
- Modify: `src/coding_agent/main.py`
- Modify: `web/src/api/socket.ts`

**Interfaces:**

- Produces: `create_app(store, coordinator, public_config) -> FastAPI`，允许测试注入，不使用全局可变单例。
- Produces: 仅测试使用的 ScriptedModel server 启动入口；生产 CLI 不接受选择 fake provider 的参数。

- [ ] **Step 1: 写服务重启集成测试**

P0 分别构造遗留 `MODEL_STREAMING`、`AWAITING_APPROVAL` 和 `TOOL_RUNNING`。断言启动恢复不会重放工具；tool-running 当前项为 `unknown`，queued 为 `skipped`；approval 中当前及后续为 `cancelled`；消息配对后 Run 为 `INTERRUPTED/SERVER_RESTART`。可能已有副作用或仍有进程的场景设置 `requires_recovery_ack`，用户确认前禁止新 Run；文案明确 P0 无法保证回收主动脱离 process group 的后代。

- [ ] **Step 2: 写主 E2E 流程**

`agent-flow.spec.ts` 使用全新临时 workspace 和 data-dir：创建 Session、发送任务、观察流式文本、批准写入、批准测试命令、看到最终回复、刷新页面并确认历史和终态恢复。

- [ ] **Step 3: 写停止与断线 E2E**

P0 的 `stop-reconnect.spec.ts` 至少覆盖流式生成中 Stop、浏览器断线后后端继续运行，以及后端重启后旧 token 失效→重新 bootstrap→snapshot/recovery-ack 恢复。审批/命令执行中的 Stop 由后端集成测试覆盖。审批/命令的额外浏览器 E2E 与更完整恢复矩阵属于 P1。每条 P0 路径最终按钮恢复 Send，历史仍可见，未开始的副作用次数不增加。

- [ ] **Step 4: 运行测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/integration/test_restart_recovery.py -v
npm --prefix web run test:e2e
```

- [ ] **Step 5: 实现启动恢复、静态资源托管和 E2E fixture**

FastAPI lifespan 中先执行恢复，再接收请求；生产模式托管 `web/dist`，开发模式由 Vite 代理。`web/playwright.config.ts` 明确设置 `testDir="../e2e"`，并配置两个 `webServer`：后端命令为 `uv run --python 3.12 tests/support/scripted_server.py` 且 `cwd=".."`，Vite server 使用配置文件所在的当前 `web/` 目录（`cwd="."` 或省略）；readiness 分别检查 `/api/health` 和页面 URL，`reuseExistingServer=false`，teardown 必须终止本次启动的进程。Scripted server 使用全新临时 workspace/data-dir，并明确清除真实模型 key 环境变量。

- [ ] **Step 6: 验证 M3**

```bash
make test
make test-e2e
make build
```

Expected: 全部退出码为 0，网络日志中没有模型 API 请求。

- [ ] **Step 7: 提交检查点（本地 commit 已预授权）**

建议提交信息：`feat: support resilient web sessions and recovery`。

---

### Task 17: P0 运行报告与四任务 smoke

**Files:**

- Create: `src/coding_agent/evaluation/__init__.py`
- Create: `src/coding_agent/evaluation/manifest.py`
- Create: `src/coding_agent/evaluation/runner.py`
- Create: `src/coding_agent/evaluation/report.py`
- Create: `src/coding_agent/evaluation/cli.py`
- Create: `evaluation/README.md`
- Create: `evaluation/schemas/manifest-v1.schema.json`
- Create: `evaluation/schemas/run-v1.schema.json`
- Create: `evaluation/schemas/summary-v1.schema.json`
- Create: `evaluation/tasks/public/manifest.toml`
- Create: `evaluation/tasks/public/` 下 4 个可再分发的小型任务目录
- Create: `evaluation/examples/run-v1.redacted.json`
- Create: `evaluation/examples/summary-v1.json`
- Create: `tests/evaluation/test_manifest.py`
- Create: `tests/evaluation/test_runner.py`
- Create: `tests/evaluation/test_report.py`
- Modify: `src/coding_agent/cli.py`
- Modify: `src/coding_agent/runtime/metrics.py`

**Interfaces:**

```text
validate_manifest(path: Path) -> EvaluationManifest
run_campaign(manifest: EvaluationManifest, config: Path, repeats: int, output_dir: Path, dry_run: bool) -> CampaignResult
summarize_campaign(input_dir: Path, output_dir: Path) -> Summary
```

`run-v1` 至少包含版本、task/repeat、Agent commit、`provider="anthropic_messages"`、配置/任务/prompt/tool-schema hash、Run state/stop reason/failure stage、模型 requests/attempts/usage、工具统计、压缩次数/前后 token/`compaction_above_target`/估算误差、分阶段 monotonic duration、workspace tree/diff hash、oracle 和 `strict_success/artifact_correct`。tool-schema hash 基于 Anthropic `{name, description, input_schema}` wire 结构；usage 分别保存 `input_tokens`、`output_tokens`、`cache_creation_input_tokens` 和 `cache_read_input_tokens`。

- [ ] **Step 1: 写 manifest 和隔离测试**

拒绝未知 schema version、路径逃逸、缺失 baseline/oracle、空或越界的 command/cwd allowlist、baseline 未按预期失败、gold 未通过。每个 repeat 必须得到不同的临时 workspace 和 data-dir；oracle 与 gold 永不复制进 Agent workspace。P0 `--yes` smoke 只执行 manifest 中逐条精确匹配的 command/cwd，其它命令返回可回灌的 policy error。

- [ ] **Step 2: 写评分和报告测试**

```python
def test_strict_success_requires_all_conditions(run_result):
    run_result.oracle_passed = True
    run_result.regressions_passed = True
    run_result.forbidden_changes = []
    run_result.detected_workspace_escape = False
    run_result.state = "COMPLETED"
    assert compute_strict_success(run_result) is True

def test_missing_usage_remains_null(run_result):
    run_result.model.usage.input_tokens = None
    summary = summarize([run_result])
    assert summary.total_input_tokens is None
```

另测 `HARNESS_SETUP`/`HARNESS_ORACLE_ERROR` 不进入能力分母、run JSON 与 summary JSON 数值一致、同名 campaign/run 禁止覆盖。

- [ ] **Step 3: 运行测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/evaluation -v
```

- [ ] **Step 4: 实现 evaluator**

runner 必须以子进程调用正式入口，argv 固定包含 `coding-agent run --config <config> --workspace <fresh> --data-dir <isolated> --prompt-file <prompt> --yes --ack-unsafe-auto-approve --command-policy <generated-policy> --report-out <run.json>`，不得 import Agent Loop 私有 helper。所有时间使用 monotonic clock；默认导出不含 prompt、工具参数、命令输出、绝对路径或 transcript。`--dry-run` 只列任务数、最大请求数、workspace 和输出位置，不调用模型，也不要求 auto-approve acknowledgement。

- [ ] **Step 5: 建立 P0 任务集并运行 smoke**

内层公开仓库放 4 个自建公开样例，每类各 1 个；每个任务均预先验证 baseline 失败、gold 通过和错误变体失败。P0 每项运行 1 次，生成版本化 run JSON 和脱敏汇总即可。

- [ ] **Step 6: 验证评测器**

```bash
uv run --python 3.12 pytest tests/evaluation tests/integration/test_headless_run.py -v
uv run --python 3.12 coding-agent-eval validate --manifest evaluation/tasks/public/manifest.toml
uv run --python 3.12 coding-agent-eval run --manifest evaluation/tasks/public/manifest.toml --repeats 1 --serial --out ../tmp/eval-dry-run --dry-run
```

Expected: 测试和 manifest 校验通过；dry-run 不产生模型调用。

- [ ] **Step 7: 提交 P0 评测检查点（本地 commit 已预授权）**

建议提交信息：`feat: add reproducible evaluation smoke tests`。

---

### Task 18: README、公开安全审计与最终交付演练

**Files:**

- Create: `README.md`
- Create: `README.txt`
- Create: `scripts/audit_public.py`
- Create: `scripts/check_readme_txt.py`
- Create: `tests/unit/test_audit_public.py`
- Create: `tests/unit/test_readme_txt.py`
- Create outside public repo: `<private-workspace>/evaluation/manifests/main-v1.toml`
- Create outside public repo: `<private-workspace>/video/演示脚本.md`
- Create outside public repo: `<private-workspace>/delivery/提交检查清单.md`
- Create outside public repo: `<private-workspace>/delivery/receipts/`
- Modify: `.gitignore`
- Modify: `Makefile`

**Interfaces:**

```text
uv run --python 3.12 scripts/audit_public.py --repo . --history
uv run --python 3.12 scripts/check_readme_txt.py README.txt
```

- [ ] **Step 1: 写审计脚本测试**

使用临时 Git 仓库 fixture，覆盖 API key/Authorization/private-key 模式、禁止路径、SQLite/log/build/test artifact、内部域名/路径、本机绝对路径和 Git 历史命中。输出只能报告规则 ID、文件、行号或 commit，不得回显疑似 secret 的原文。

- [ ] **Step 2: 写 README.txt 限制测试**

按 Unicode code point 统计正文不超过 1000 个字符；检查仓库 URL、最短运行方法和功能摘要存在，并拒绝凭据模式。README.md 还必须说明 Anthropic-compatible Messages 单协议范围、`ANTHROPIC_API_KEY`、Make 命令、架构、审批、`--yes` 风险、`run_command` 非沙箱边界、评测和真实模型测试开关。

增加协议残留回归检查：公开交付代码、依赖和配置中不得出现 `openai` 包/import、`AsyncOpenAI`、`chat.completions`、`finish_reason`、`stream_options` 或 `max_completion_tokens`；README 可以在“不支持的第二协议”段落中提及 OpenAI 名称。

- [ ] **Step 3: 运行测试并确认红灯**

```bash
uv run --python 3.12 pytest tests/unit/test_audit_public.py tests/unit/test_readme_txt.py -v
```

- [ ] **Step 4: 实现文档和质量门禁**

`make check` 按设计依次运行 lint、后端/前端默认测试、E2E 和生产构建。发布审计由两个显式 uv 命令执行，避免悄悄扩大已确认的 Makefile 接口。不得在 Makefile 中提供删除目标。

- [ ] **Step 5: 完成离线全量验证**

```bash
make check
uv run --python 3.12 scripts/audit_public.py --repo . --history
uv run --python 3.12 scripts/check_readme_txt.py README.txt
git status --short
git ls-files
git log --oneline --decorate
git fsck --full
```

Expected: 所有检查通过；`doc/` 仅跟踪两份已审计的公开设计文档，tracked files 中没有 `doc/ref/`、调研原始资料、`tmp/`、`.superpowers/`、配置密钥、数据库、日志、原始评测结果或内部材料；记录仓库创建时间、首个提交和连续历史供最终人工 gate 核验。

- [ ] **Step 6: 完成 P0 真实模型与评测证据**

在用户明确提供环境变量后，运行设计稿要求的真实主链冒烟，并运行 4 个公开任务各 1 次。原始 transcript/results 留在仓库外私有目录，只将脱敏聚合值和示例放入公开仓库。12×3 campaign、消融和 SWE-bench 属于 P1。

- [ ] **Step 7: 准备演示与提交物**

使用公开 demo workspace 录制不超过 2 分钟、200 MB 的 MP4：发送任务、读取、固定审批坞批准写入、批准测试、最终结果；其中预留 15–30 秒讲解自研 Messages 解析、Agent Loop、上下文压缩、本地工具执行/结果回灌和终止错误处理。录制前隐藏环境变量值、本机私有路径、通知和终端历史。

生成最终“真实姓名.zip”后，用 `zipinfo -1` 确认成员恰为 `README.txt` 和一个 `.mp4`；用 `wc -m`/项目检查脚本确认 README 不超过 1000 字，用 `ffprobe` 和文件字节数确认视频不超过 120 秒和 200 MB，并从未登录浏览器验证 README 中的公开仓库 URL。

- [ ] **Step 8: 本地提交与发布检查点（本地 commit 已预授权，远端操作仍须授权）**

建议最后一个功能提交信息：`docs: complete usage evaluation and release guidance`。只有在用户确认安全审计结果后才将内层仓库改为 Public；从无权限环境验证默认分支和完整历史可访问，取得用户当次授权后在截止前执行最终 push，截止后不得再 push。不得 squash 或改写已经推送的提交历史。

最终将仅含两项文件的 zip 上传至题目指定表单 `https://table.nju.edu.cn/dtable/forms/283d6c7d-475a-4f41-8baf-d3f45966ef2d/`，记录提交时间和回执；若重复提交，以最后一次回执为准。表单上传是人工外部操作，执行前必须由用户确认。

`<private-workspace>/delivery/receipts/` 可能包含个人或提交系统信息，只在本地保存并加入私有工作区 ignore，不提交到任何远端。

---

### Task 19: GitHub Actions CI（离线门禁）

> 本任务由 `doc/CI-接入计划.md` 合并而来；该计划文档并入后删除，以本节为唯一权威。Task 19 属于 P0 交付后的工程加固，不阻塞任何 P0 里程碑。

**Files:**

- Create: `.github/workflows/ci.yml`
- Modify: `README.md`（§10 增加 CI 状态徽章一行）
- Delete: `doc/CI-接入计划.md`（内容已并入本任务）

**Interfaces:**

```yaml
# 触发：push 到 main + 全部 pull_request；单 job `check`，runs-on ubuntu-latest，timeout-minutes 20
# permissions: contents: read（最小权限，本流水线不使用 GITHUB_TOKEN）
# 步骤顺序（失败即短路，镜像本地 make check 但拆到步骤级便于定位）：
#   actions/checkout@v5
#   astral-sh/setup-uv@v9.0.0  (enable-cache: true, python-version: "3.12"——必须精确版本，见下方"已踩过的坑"1)
#   actions/setup-node@v4      (node-version: 22, cache: npm, cache-dependency-path: web/package-lock.json——必须 ≥22，见坑 3)
#   uv sync --frozen --python 3.12 --all-groups
#   npm --prefix web ci
#   npm --prefix web exec -- playwright install chromium --with-deps
#   ruff check src tests scripts && npm --prefix web run lint
#   npm --prefix web run build        （必须在后端测试之前，见坑 2）
#   uv run --python 3.12 pytest --ignore=tests/live
#   npm --prefix web run test
#   npm --prefix web run test:e2e
#   scripts/audit_public.py --repo .（不带 --history） && scripts/check_readme_txt.py README.txt
#   coding-agent-eval validate --manifest evaluation/tasks/public/manifest.toml
```

**已踩过的坑（首跑三连失败的根因，修复已内联为 ci.yml 注释，勿回退）：**

1. **`astral-sh/setup-uv` 不发布浮动 major tag**：仓库只有 `v9.0.0` 这类精确 tag，没有裸 `v9`（与 `actions/checkout@v5`、`actions/setup-node@v4` 的惯例不同）。写 `@v9` 会在 GitHub 解析阶段直接失败（本地 YAML 校验发现不了）。**必须钉精确版本**；升级时手动改。
2. **后端测试隐式依赖 `web/dist`**：`tests/integration/test_headless_run.py` 会真实启动 `serve`，而 `serve` 挂载 `web/dist` 静态目录。若 build 排在后端测试之后，全新 checkout 必挂 `RuntimeError: Directory .../web/dist does not exist`。本地验证必须先移走 `web/dist` 模拟全新 checkout——残留的本地 dist 会掩盖此缺陷（第二次失败即由此而来）。
3. **Node 下限是 22.14，不是 20**：依赖链 `jsdom 30 → undici 8.10`，其 `CacheStorage` 调用 `webidl.util.markAsUncloneable`，该 API Node ≥ 22.14 才存在。Node 20 上 jsdom 环境完全无法启动（vitest worker 全崩、零测试执行），且**本地 node 24 无法复现**——只能靠在目标 Node 版本（nvm exec 22）下实测发现。未来升级 jsdom/undici 时须重新确认 Node 下限。

另两条经验：步骤顺序镜像 `make check` 但**不能**直接 `make check`（需要步骤级失败定位 + `--with-deps` 差异）；本地验证"全绿"的可信度取决于是否消除了本地残留（dist、node_modules、API key 环境变量），最严格验证 = 移走 dist + `env -u` 两个 key + 目标 Node 版本。

- [x] **Step 1: 写 CI 脚本**

按上述接口创建 `.github/workflows/ci.yml`。硬约束：**CI 不进行任何需要 API key 的测试**——`tests/live/` 由 `--ignore` 显式排除（`tests/conftest.py` 的 `RUN_LIVE_TESTS` gate 是第二道防线，且 CI 环境本无任何 secret）；评测只跑 `validate`（manifest 三态校验），不跑真实模型；E2E 使用 ScriptedModel 驱动本地 scripted_server，无网络。不需要操作者提供任何资源：无 secret、无部署、默认 `GITHUB_TOKEN` 即可。

- [x] **Step 2: 本地静态校验**

```bash
.venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
.venv/bin/python scripts/audit_public.py --repo .
```

Expected: YAML 可解析；工作树审计 0 findings（`.github/` 属正常工程目录，不在禁止路径清单内）。

- [x] **Step 3: 首跑观察与迭代**

提交并 push 后在 GitHub 仓库 Actions 页签观察首跑。已知首跑风险与对策：E2E `webServer` 的 30 秒启动超时在 CI 冷启动环境可能偏紧（必要时单独调 `web/playwright.config.ts` 的 `timeout`，调整需先过本地 E2E 回归）；uv 缓存以 `uv.lock` 为键，仓库已提交锁文件，命中率高；私有仓库免费额度 2000 分钟/月，单次约 8–12 分钟，远低于额度（转 Public 后无限）。首跑失败的环境差异修复属于正常迭代，预计 1–2 轮。

> **实际执行记录（2026-08-29）**：首跑实际经历三轮环境差异失败后于第四次成功（run `33247066889`，1m43s——runner 缓存生效，远快于预估的 8–12 分钟；E2E webServer 30s 超时最终未成为问题）。三个根因与修复详见上方"已踩过的坑"；对应的四个提交为 `27533bd`（workflow 本体）→ `8af4f91`（钉 setup-uv 精确版本）→ `39a52dc`（build 提前）→ `acd6a36`（node 22）。修复均以 ci.yml 内联注释 + 本节文字双重固化，防止回退。

- [x] **Step 4: 徽章与验证收口**

```bash
make check
uv run --python 3.12 scripts/audit_public.py --repo .
```

Expected: 本地全绿；README §10 出现指向 `hanhaiqingchuan/my-coding-agent` 的 CI 状态徽章且 URL 从未登录环境可访问（转 Public 前徽章对匿名访问不可见，属预期，转 Public 后自动生效）。

- [x] **Step 5: 提交检查点（本地 commit 已预授权，push 需当次确认）**

建议提交信息：`ci: add github actions workflow`。

**明确不做（本任务范围外）**：矩阵测试（P0 只支持 Python 3.12，矩阵无意义）；发布/部署步骤；覆盖率上报（Codecov 等第三方服务超出题目需要）；live 测试的任何 CI 触发（需要真实 key 且产生费用，永远不做）。

---

### Task 20: 评测扩展——扩容任务集、确定性与模糊指标、结果持久化

> 定位：在 Task 17 既有评测器（manifest/runner/report、`run-v1`/`summary-v1`）之上**增量扩展**，不替换任何既有接口。模糊指标自研、复用同一个 Anthropic Messages API 作裁判；**不引入 ragas 等第三方评测框架**（本项目禁用 agent 框架，且 ragas 的 agent 指标要求 OpenAI 兼容端点与参考轨迹，仅借鉴其指标设计思路：工具调用准确率、目标达成率、带理由的评分）。

**Files:**

- Create: `src/coding_agent/evaluation/tasks_extra.py`（12 任务集生成与校验）
- Create: `src/coding_agent/evaluation/judge.py`（LLM 裁判：评分请求构造、响应解析、裁判记录）
- Create: `src/coding_agent/evaluation/history.py`（历史索引：campaign 目录扫描、索引构建与查询）
- Create: `evaluation/tasks/public/` 下 8 个新任务目录（4 类各 +2，凑齐 12）
- Create: `evaluation/schemas/judgement-v1.schema.json`
- Modify: `src/coding_agent/evaluation/runner.py`（可选 judge 挂钩）
- Modify: `src/coding_agent/evaluation/report.py`（确定性指标汇总 + judge 聚合）
- Modify: `src/coding_agent/evaluation/cli.py`（`run --judge`、`history` 子命令）
- Modify: `evaluation/README.md`
- Create: `tests/evaluation/test_tasks_extra.py`、`test_judge.py`、`test_history.py`

**Interfaces:**

```text
build_task_set(public_dir: Path) -> list[EvaluationManifest]   # 12 任务：4 类 × 3
judge_run(run_document: dict, transcript_excerpt: Mapping, settings) -> Judgement
Judgement(scores: Mapping[str, int], rationale: str, judge_model: str, prompt_version: str, schema_version="judgement-v1")
# 模糊指标（1-5 分制 + 理由，单一裁判请求、结构化 JSON 输出）：
#   task_completion   任务目标是否达成（相对 prompt 与 oracle 事实）
#   process_quality  工具选择与顺序是否合理（读后写、失败后自纠、无冗余调用）
#   communication    最终回复是否如实、简洁、说明验证状态
scan_campaigns(results_root: Path) -> list[CampaignSummary]   # 历史索引：目录扫描 + 缓存
# 确定性指标全部来自既有 run-v1，不重复采集：tools.by_name、totals.round_count、
# durations.*、totals.input/output_tokens、retries、compaction
```

- [ ] **Step 1: 扩容公开任务集并验证三态**

按设计方案 §18.4 的四类（新建单文件功能 / 既有函数局部修改 / 搜索定位后改两文件 / 600 行以上文件小改动）在 4 个既有任务基础上每类新增 2 个，共 12 个；沿用既有目录结构（prompt.md、baseline/gold/error、oracle、manifest 摘要钉死）。离线测试：全部任务通过 `coding-agent-eval validate`（baseline 失败、gold 通过、error 变体失败）。

- [ ] **Step 2: 写 judge 失败测试（TDD）**

`judge.py` 的裁判请求：复用 `AnthropicMessagesModel` 适配器（同一 `ModelSettings`，可指向同或不同模型），单次请求、`stream=true`、无 tools、要求输出固定 JSON。测试覆盖：合法 JSON 解析、非法 JSON/缺字段/越界分数的重试一次后失败落 `judge_error`（不中断 campaign）、裁判记录含 judge_model 与 prompt_version、裁判 prompt 与 transcript 摘录不含凭据、离线用 fake model 驱动全部路径。

- [ ] **Step 3: 写 history 索引失败测试**

`history.py` 扫描 results_root 下的 campaign 目录（每个含 runs.jsonl 或 runs/*/run.json 即算），构建递增索引（campaign_id、时间、任务数、strict_success 率、judge 均分、模型标识），只读、可缓存、损坏目录跳过并记录。测试：空目录、正常 campaign、损坏 JSON、多 campaign 排序。

- [ ] **Step 4: 实现 runner 挂钩与报告聚合**

`coding-agent-eval run --judge` 在每个 run 结束后调用 judge（transcript 摘录 = run-v1 已有字段 + 最终 assistant 文本，不新增原始 transcript 采集），judge 结果写入 `runs/<task>/<repeat>/judgement.json` 并进 summary 聚合（均分、覆盖率、judge_error 数）。`history` 子命令打印历史索引。既有无 judge 路径行为不变。

- [ ] **Step 5: 验证（离线全量）**

```bash
uv run --python 3.12 pytest tests/evaluation -v
uv run --python 3.12 pytest -q
uv run --python 3.12 coding-agent-eval validate --manifest evaluation/tasks/public/manifest.toml
uv run --python 3.12 coding-agent-eval history --results <tmp>   # 空目录与样例目录
make check
```

Expected: 全部退出码 0；judge 与 history 的离线测试用 fake model / 临时目录；CI 不受影响（仍只跑 validate）。

- [ ] **Step 6: 提交检查点（本地 commit 已预授权，push 需当次确认）**

建议提交信息：`feat: extend evaluation with judged metrics and history`。

**明确不做**：不引入 ragas/deepeval 等第三方评测库；不做多裁判投票与裁判模型矩阵（P1 消融）；模糊指标不进入 `strict_success` 判定（能力分母只由确定性五条件构成，见设计方案 §18.5）；裁判不读模型上下文之外的数据。

---

### Task 21: 评测结果网页——统一查看历史 campaign 与指标

> 定位：**只读结果展示页**，复用既有 FastAPI 静态托管模式；不新建独立服务进程、不新增 agent 能力。运行（真实 campaign）仍走 CLI，网页负责"看"。

**Files:**

- Create: `src/coding_agent/evaluation/web.py`（结果 API：campaign 列表/详情/单 run + judgement）
- Create: `web/src/features/evaluation/`（EvaluationsPanel、EvaluationDetail、JudgementCard）
- Modify: `src/coding_agent/main.py`（serve 挂载 `/api/evaluations/*` 与页面路由）
- Modify: `web/src/App.tsx`（左栏加入 Evaluations 入口）
- Create: `web/src/features/evaluation/*.test.tsx`
- Modify: `README.md`（评测章节补网页用法）

**Interfaces:**

```text
GET /api/evaluations                     -> CampaignSummary[]（来自 history.py 索引）
GET /api/evaluations/{campaign_id}       -> CampaignDetail（tasks、每任务 runs、聚合指标）
GET /api/evaluations/{campaign_id}/runs/{task_id}/{repeat} -> run-v1 文档 + judgement
# 页面：/evaluations 路由（React），深链可分享；数据只读，无写操作
```

- [ ] **Step 1: 写结果 API 失败测试**

只读端点、JSON 契约、不存在 campaign 404、损坏 judgement 的容错降级、同一 Host/Origin/CSRF 防护面（GET 只读，沿用 Task 13 裁定：读端点不要求 CSRF token，但 Host/Origin 仍校验）。

- [ ] **Step 2: 写前端组件失败测试**

EvaluationsPanel 列表（campaign、时间、任务数、成功率、judge 均分）、详情页（每任务行：确定性指标列——轮次/工具调用/token/耗时 + strict_success + judge 三项分数与理由折叠展示）、空态与加载态、history 数据损坏时的优雅降级。

- [ ] **Step 3: 实现后端只读 API 与前端页面**

`evaluation/web.py` 只读消费 history.py 与 run-v1 文档，无状态、无写路径；前端复用既有设计语言（深色工作台、折叠详情、状态色），新增左栏入口与 `/evaluations` 路由。不新增依赖。

- [ ] **Step 4: 端到端验证**

```bash
npm --prefix web run test && npm --prefix web run build
make test-e2e          # 既有 5 条不受影响；若加评测页 E2E，用离线 fixture campaign
uv run --python 3.12 pytest -q
```

Expected: 全绿；用一个本地生成的离线样例 campaign（fake model 产物）手动验证页面渲染：列表→详情→单 run→judgement 折叠，截图留证。

- [ ] **Step 5: 提交检查点（本地 commit 已预授权，push 需当次确认）**

建议提交信息：`feat: add evaluation results dashboard`。

**明确不做**：不从网页发起评测运行（运行仍走 CLI，避免网页长连接与审批歧义）；不做评测结果编辑/删除；不做跨机器数据同步；页面不含任何凭据或绝对路径。

---

## Execution Order and Checkpoints

P0 关键路径按 Task 1 → 12 顺序执行；Task 7 先冻结 `ModelRequest/ModelMessage/AssistantTurn/Usage/typed exceptions`，Task 9 才消费这些契约，不并行猜测接口。Task 12 的正式 headless 纵向链路通过后，立即并行起草 Task 18 的 README.txt、demo workspace、视频脚本和发布 checklist，再继续 Task 13–15 与 Task 16 的主链/Stop E2E。随后完成 Task 17 的 4 个公开任务各 1 次 smoke 和 Task 18 发布 gate。P0 可提交后才进入 P1。

四个里程碑检查点：

1. **M1：** `uv run --python 3.12 pytest tests/unit/test_config.py tests/unit/test_models.py tests/unit/test_state_machine.py tests/unit/test_cancellation.py tests/unit/test_sqlite_store.py tests/integration/test_store_recovery.py -q`
2. **M2：** `uv run --python 3.12 pytest tests/unit tests/integration/test_agent_loop.py tests/integration/test_coordinator.py tests/integration/test_stop_paths.py tests/integration/test_headless_run.py -q`
3. **M3：** `make test && make test-e2e && make build`
4. **M4：** `make check` 加两条显式发布审计命令，随后人工检查公开文件、完整历史、演示画面和最终压缩包。

任何里程碑失败都留在当前任务修复，不把失败测试或未核实行为推迟到下一阶段。真实模型或网络不稳定不得阻塞 M1–M3；只有 P0 smoke/P1 campaign 的显式 live/evaluation 步骤允许访问模型服务。

## P1 Deferred Work

P0 发布 gate 通过后再为以下内容编写独立增量计划：`pause_turn` continuation 与持久化、非流式 Messages、Session 重命名/删除、JSONL 导出、dirfd/`openat` 路径加固、扩展恢复/E2E 矩阵、12×3 campaign、压缩消融、SWE-bench 和额外视觉优化。P1 不得在 P0 实现中留下半完成入口或可配置但不可用的分支。

## Spec Coverage Matrix

| 设计要求 | 实施任务 |
|---|---|
| 技术栈、Python 3.12、uv、配置优先级、Makefile | Task 1 |
| Run 状态、停止原因、软工作流程与取消 | Task 2、11、12 |
| SQLite、canonical transcript、事件、幂等和重启恢复 | Task 3、12、16 |
| workspace 边界及三个本地工具 | Task 4–6 |
| Anthropic-compatible Messages 请求、SSE content blocks、tool use/result 和协议错误 | Task 7 |
| 429/5xx/连接错误重试与取消 | Task 8、12 |
| 上下文预算、所有 user 原文保留、裁剪与压缩 | Task 9–10 |
| Agent Loop、审批顺序、轮次与防循环 | Task 11 |
| headless 正式入口、usage 和运行报告 | Task 11–12、17 |
| P0 REST/WebSocket、bootstrap、快照与事件补发 | Task 13–14 |
| 美观工作台、Send/Stop、固定审批坞与状态详情 | Task 14–15 |
| 浏览器断线、服务重启和无副作用重放 | Task 3、12、13、16 |
| 离线自动化测试、P0 四任务 smoke 与 P1 12×3 campaign | Task 1–17 |
| README、README.txt、公开安全审计和演示视频 | Task 18 |

自审结论：题目要求的对话历史与上下文管理、工具定义和本地执行、模型输出解析、循环终止条件及错误处理均有对应实现与测试任务；未引入被禁止的 Agent 框架、服务端文件/执行工具或第二套 Agent Loop。
