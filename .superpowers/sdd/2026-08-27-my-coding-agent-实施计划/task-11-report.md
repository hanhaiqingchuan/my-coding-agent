# Task 11 实施报告：Approval Gate、Agent Loop 与可审计闭环

## 实现结果

- 新增统一 `AgentLoop`，通过依赖注入组合 `SQLiteStore`、`ContextBuilder`、`Compactor`、`ModelGateway`、`RetryingInvoker`、`ToolRegistry`、`ApprovalGate`、`RunMutationGate` 与 `EventPublisher`；runtime 未依赖 FastAPI。
- 用 `ScriptedModel` 完成首条离线纵向链路：user → `read_file` → `write_file` 显式批准 → `run_command` 显式批准 → final assistant。工具按模型原序串行执行，assistant/tool group 在每次执行时保持 `pending_tools`，所有结果齐备后一次提交为 canonical history。
- `ApprovalGate` 保存后端冻结的 `PreparedToolCall`，支持交互式 resolve、`--yes` 风格自动批准和取消唤醒；取消后不会再次投递 stale approval。`read_file` 自动执行，未知工具和参数错误直接形成普通 tool error，不进入审批。
- 审批 requested/resolved 均写 durable event；`--yes` 只改变决策来源，不绕过审计。write 审批时将 baseline hash 与完整 target/preview/metadata 同事务固化到审计事件。
- Reject 将当前调用记为 `rejected`、剩余调用记为 `skipped`，并补齐 tool results 后让模型重新规划。Stop 在 effect-start 前将当前调用记为 `cancelled`、后续记为 `skipped`；effect 已开始后保留真实 `succeeded`/`failed`/`cancelled` 结果。
- `RunMutationGate` 用同一进程级临界区串行化全局 active-run claim、approval resolve、effect-start、Stop 与终态提交。Stop 先持久化 `CANCELLING` 再触发内存 token；late final 不能覆盖 Stop。最终 assistant 消息和 terminal run 状态在同一 SQLite 事务提交。
- `EventPublisher` 用 session lock 串行化 snapshot/subscribe cut 与 live publish；subscriber 按 session 隔离，发布采用非阻塞 queue，慢消费者被断开并可按 durable seq 重连补读；unsubscribe 明确释放订阅。
- 循环终止语义已覆盖：仅完整、合法、非截断且无 tool calls 的响应正常完成；text + tools 仍执行；空回复只重试一次；纯文本 `max_tokens`、带工具 `max_tokens`、refusal、pause_turn、预检 context overflow、provider overflow、最终轮和协议错误均产生结构化 outcome。
- 重复调用阈值为 3：前两次执行，第三次回灌 `REPETITION_DETECTED`，第四次相同提案以 `DOOM_LOOP` 停止。工具参数错误允许两轮连续自纠；超过预算以结构化 `DOOM_LOOP` 收束，避免新增设计外 stop reason。
- provider context overflow 只允许一次强制压缩与 context rebuild；第二次返回 `FAILED/CONTEXT_OVERFLOW`。Task 9 的 deterministic `ContextOverflow` 在任何模型请求记录建立前直接失败。
- 主模型与压缩模型的每个请求均写 `model_requests`，保存 kind、round、attempt、基于实际 max tokens/tool 配置的非敏感 hash、开始/结束时间、provider usage、网络重试数和累计等待。run 汇总 usage 包括 compaction 成本，但 `round_count` 只计主模型调用。
- 流失败的已接收文本保存为 `interrupted` assistant 记录，永不进入 `load_committed_transcript()`；截断/refusal/pause 的展示文本采用相同非 canonical 规则。
- plan/execute/verify/reflect 仍只存在于 system prompt，模型可见工具保持 `read_file`、`write_file`、`run_command` 三项，没有 complete/task_done 工具或认知阶段状态。

## 最小兼容扩展

- `SQLiteStore` 新增 run 查询、审批请求审计、interrupted turn、模型请求 lifecycle、retry schedule，以及 final-turn + terminal-state 原子提交方法；未建立第二事实源。
- `ToolRegistry` 新增对已 prepare 调用的统一 `execute()` 分派；三个工具自身逻辑未复制。
- `Compactor.compact()` 新增可选 request invoker，使 AgentLoop 可为每个 compaction chunk 复用唯一 retry/metrics 路径；原有直接调用保持兼容。
- `ToolExecutionState` 增加设计和前端契约已要求的 `REJECTED`；状态机增加 tool-error/reject 后回到 `BUILDING_CONTEXT` 的明确边。

## TDD 证据

### RED

1. 首批 approval/publisher/coordinator 测试运行时，3 个测试模块均以 `ModuleNotFoundError: coding_agent.runtime` 收集失败，证明 runtime 尚不存在。
2. 首条纵向闭环测试以 `ModuleNotFoundError: coding_agent.runtime.loop` 失败；最小 loop 建立后才转绿。
3. 终止策略批次首次运行 13 项，6 项按预期失败：空响应被误报完成、special 文本未记录 interrupted、重复/参数错误无限请求脚本。
4. provider overflow 两项首次运行均失败：未调用 compaction，第二次 overflow 被误当正常完成。
5. Stop-after-effect 协作取消测试稳定复现 `PENDING_TOOL_GROUP_EXISTS`，根因为取消异常绕过当前 tool result settlement。
6. late-final/Stop 测试稳定复现 `CANCELLING -> COMPLETED` 非法转换，且揭示 final message 与终态不是同事务。
7. stale approval 测试稳定复现取消后的调用仍留在投递 queue。
8. 真实 ToolRegistry 闭环的 baseline 断言首次失败为 `None`，证明准备阶段的 write baseline 尚未持久化。

### GREEN / REFACTOR

- 每个失败切片均在最小实现后单独复跑：Approval/publisher/coordinator 12 项通过；主链、终止、重试、压缩、Stop 竞态和真实工具边界随后分别转绿。
- 修复 Stop-after-effect 的根因：正在执行的调用捕获协作取消并先写 cancelled tool result；Stop 事务已把后续 queued 调用补为 skipped，因此 group 可完整提交后再进入 `CANCELLED/USER_STOP`。
- 修复 late-final 的根因：final assistant 与 terminal run 合入单一 store 事务，并由同一 mutation gate 与 Stop 排序；Stop 获胜时不提交迟到 final。
- REFACTOR 后保留一条 AgentLoop；公共工具执行通过 `ToolRegistry.execute()`，压缩通过已有 `Compactor`，未复制工具/压缩实现。

## 验证

- 聚焦：`uv run --python 3.12 pytest tests/unit/test_approval.py tests/unit/test_event_publisher.py tests/integration/test_agent_loop.py tests/integration/test_coordinator.py -q` → 47 passed。
- 全套：`uv run --python 3.12 pytest -q` → 262 passed, 1 skipped；skip 为既有 macOS Unix-socket 路径长度条件。
- 静态：`uv run --python 3.12 ruff check .` → 通过。
- 格式：触及的 Python 文件 `ruff format --check` → 通过。
- `git diff --check` → 通过。
- 测试使用 `ScriptedModel`/本地临时 workspace，不访问网络。

## 范围说明

- `pause_turn` continuation 按 P0 约束未实现，明确收束为 `STOPPED/PAUSE_TURN`。
- publisher 保持进程内实时分发；durable replay 继续以 SQLite `events` 和 session `seq` 为事实源。
- 未执行远端写入。

## Fix Round 1

### 变更

- 将真实流式协议边界的 `ModelProtocolError(code="INCOMPLETE_TOOL_CALL")` 单独映射为 `STOPPED/INCOMPLETE_TOOL_CALL`，不进入工具 stage/execute。`MessageStreamAssembler` 把已聚合的 provider usage 附在该结构化错误上，request record 保存 `21/9/5/8` 四项 usage；部分文本仅保存为 `interrupted`，不进入 canonical transcript。
- 将 cancellation token 注册改为异步 `RunMutationGate.register_cancellation()`：token 写入与 SQLite run 状态读取共用 Stop/effect 的同一临界区。若 Stop 已先持久化为 `CANCELLING`，注册立即触发 token，AgentLoop 不做 `STARTING → BUILDING_CONTEXT`，直接配对清理并收束 `CANCELLED/USER_STOP`。
- `EventSubscription` 新增 snapshot cut/last seq；`subscribe_locked(..., after_seq=snapshot.snapshot_seq)` 后，live publish 丢弃 `seq <= cut` 的重复事件，并继续投递 cut 后事件。测试用真实 SQLite snapshot 和延迟 publish 并发任务覆盖 commit-before-snapshot 与 commit-after-cut 两侧。
- `EventPublisher` 默认 queue 从 asyncio 的无限 `0` 改为有限 `256`，并拒绝所有非正配置；默认配置下慢消费者队列满后会被断开，快速消费者继续接收。
- 移除原先手造 `AssistantTurn(MAX_TOKENS, tool_use)` 的不可能集成用例，替换为 `AnthropicMessagesModel → MessageStreamAssembler → AgentLoop` 的真实生产链路测试。

### RED → GREEN 证据

1. 真实 assembler 截断工具调用：
   - RED：`uv run --python 3.12 pytest tests/integration/test_agent_loop.py::test_real_assembler_incomplete_tool_call_maps_to_stopped_and_keeps_usage -v` → 1 failed；实际为 `FAILED/MODEL_PROTOCOL_ERROR`。
   - GREEN：同一测试加两项 assembler 协议回归 → 3 passed。
2. Stop-before-registration：
   - RED：`uv run --python 3.12 pytest tests/integration/test_agent_loop.py::test_stop_before_loop_registers_token_finishes_cancelled_without_model_call -v` → 1 failed；稳定复现非法 `CANCELLING -> BUILDING_CONTEXT`。
   - GREEN：该测试与四组 Stop/effect 次序测试同跑 → 5 passed。
3. snapshot/live cut：
   - RED：真实 SQLite 并发测试 → 1 failed；`subscribe_locked` 无 cut 契约。
   - GREEN：同一测试 → 1 passed；快照内事件不重复、cut 后事件恰好投递一次。
4. 默认有限队列：
   - RED：默认慢消费者与非正配置测试 → 2 failed；默认队列实际 `maxsize=0` 且接受零值。
   - GREEN：同一命令 → 2 passed；默认慢订阅被隔离，快速订阅不中断。

### 最终验证

- Task 11 + 协议聚焦：`uv run --python 3.12 pytest tests/integration/test_agent_loop.py tests/integration/test_coordinator.py tests/unit/test_event_publisher.py tests/unit/test_approval.py tests/unit/test_message_assembler.py tests/unit/test_anthropic_messages.py -q` → 101 passed。
- 全套：`uv run --python 3.12 pytest -q` → 266 passed, 1 skipped；skip 仍为既有 macOS Unix-socket 路径长度条件。
- `uv run --python 3.12 ruff check .` → 通过。
- 本轮触及文件 `ruff format --check` → 通过。
- `git diff --check` → 通过。
