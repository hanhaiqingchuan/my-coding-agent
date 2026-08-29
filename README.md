# My Coding Agent

面向本地代码仓库的 coding agent：通过 Anthropic-compatible Messages API 与模型多轮对话，自主读取文件、提出文件修改、执行命令，并根据工具结果继续工作。使用体验对齐 Codex、OpenCode 一类 coding agent，而不是一次性聊天机器人。

对话历史与动态上下文管理、模型流式输出与 tool call 的语义聚合校验、工具定义与本地执行、Agent Loop 与终止条件、用户停止/网络重试/错误分类/重启恢复、前后端状态同步全部由本项目自行实现。仅使用官方 `anthropic` Python 客户端完成 HTTP、认证与 SSE 帧解析；不使用任何 Agent 框架或 SDK。

完整设计见 [`doc/项目设计方案.md`](doc/项目设计方案.md)，实施计划见 [`doc/2026-08-27-my-coding-agent-实施计划.md`](doc/2026-08-27-my-coding-agent-实施计划.md)。

---

## 1. 环境要求

- macOS 或 Linux 等 POSIX 系统。`run_command` 依赖 POSIX 进程组语义，Windows 不在支持范围。
- Python **3.12**，且只通过 [`uv`](https://docs.astral.sh/uv/) 管理环境、依赖和运行入口。
- Node.js 与 npm，用于构建 React + TypeScript + Vite 前端。
- 一个 Anthropic-compatible Messages API 服务的访问凭据。

## 2. 安装

```bash
make install
```

`make install` 等价于下面四条底层命令（README 保留等价命令便于定位问题）：

```bash
uv venv .venv --python 3.12          # 仅当 .venv 不存在时执行
uv sync --frozen --python 3.12 --all-groups
npm --prefix web ci
npm --prefix web exec -- playwright install chromium
```

Python 依赖只经 `uv` 安装；不要使用 `pip install`、系统 Python 或另建虚拟环境。

### Linux / Windows WSL

命令与 macOS 完全相同：安装 [uv](https://docs.astral.sh/uv/) 与 Node.js（apt、nvm 均可）后直接 `make install`。Python 一律来自 uv 托管并安装进 `.venv` 的 3.12——Makefile 与测试不依赖操作系统自带的 Python（测试子进程直接使用 `.venv` 内的解释器）。WSL/无头 Linux 唯一可能多出的一步是补齐 Chromium 的系统库：

```bash
npm --prefix web exec -- playwright install-deps chromium   # 需要 sudo
```

## 3. 配置

仓库提交 `config.example.toml` 作为模板。复制一份为 `config.toml` 后按需修改：

```bash
cp config.example.toml config.toml
```

`config.toml` 已被 `.gitignore` 忽略，不会进入仓库。默认读取启动目录下的 `config.toml`，也可用 `--config` 指定其它路径。各字段含义见 `doc/项目设计方案.md` §15。

```toml
[server]
port = 8000
open_browser = true

[model]
base_url = "https://api.anthropic.com"
model = "claude-model-name"
api_key_env = "ANTHROPIC_API_KEY"
context_window = 64000
max_output_tokens = 8192
stream = true
```

**凭据只通过环境变量提供。** 配置里只保存环境变量的**名字**（`model.api_key_env`），不保存凭据本身：

```bash
export ANTHROPIC_API_KEY="<你的密钥>"
```

修改 `model.api_key_env` 即可改用其它变量名。禁止把密钥写进 `config.toml`、Make 变量或 `ARGS`。启动时若该变量为空，程序以 `CONFIG_ERROR` 退出（退出码 2）并只打印变量名，不回显取值；后端日志、异常、网页配置快照同样只显示变量名或“已配置”。

启动还会校验模型名与 URL、窗口与输出预算关系、`stream = true`、`0 < compact_target_ratio < compact_trigger_ratio < 1`、`max_rounds >= 2`、正数超时和 workspace 是否可用，错误信息指出具体字段。

## 4. 启动网页服务

```bash
export ANTHROPIC_API_KEY="<你的密钥>"
make start CONFIG=config.toml ARGS='--workspace /path/to/project --open'
```

`make start` 先执行 `make build`（`npm --prefix web run build`，产物 `web/dist` 由 FastAPI 托管），再启动生产形态的单进程服务。等价的底层命令：

```bash
npm --prefix web run build
uv run --python 3.12 coding-agent serve \
  --config config.toml \
  --workspace /path/to/project \
  --open
```

浏览器打开 `http://127.0.0.1:8000`。服务固定监听 `127.0.0.1`，没有登录和鉴权，也不提供远程 host 开关。未传 `--workspace` 时，网页先显示目录选择器；`--workspace` 会成为初始 Session 的目录，之后仍可在网页上为新 Session 选择其它目录。

`coding-agent serve` 的参数：

| 参数 | 说明 |
|---|---|
| `--config PATH` | 配置文件路径；省略时读取启动目录下的 `config.toml` |
| `--workspace PATH` | 初始 Session 的工作目录 |
| `--data-dir PATH` | SQLite 与运行数据目录；默认启动目录下的 `.coding-agent/` |
| `--port PORT` | 覆盖 `server.port` |
| `--eval-results PATH` | 评测结果根目录（含多个 campaign 子目录）；默认 `<data-dir>/evaluation-results` |
| `--open` | 启动后打开浏览器 |
| `--yes` | 受信任模式，跳过所有审批（见 §8） |

开发模式使用两个终端，Vite 代理 `/api` 与 WebSocket：

```bash
# terminal 1
make dev-api CONFIG=config.toml ARGS='--workspace /path/to/project'

# terminal 2
make dev-web
```

## 5. headless 运行

同一套 composition root 也提供不带浏览器的正式入口，用于自动化和评测（不是另一条 Agent Loop）：

```bash
uv run --python 3.12 coding-agent run \
  --config config.toml \
  --workspace /path/to/fresh-task \
  --data-dir /path/to/isolated-state \
  --prompt-file task.txt \
  --report-out run.json
```

`--workspace`、`--data-dir`、`--prompt-file`、`--report-out` 均为必填；`--config` 省略时读取启动目录下的 `config.toml`。配置文件不存在时以 `CONFIG_ERROR` 退出（退出码 2）并指出缺少的路径，不会退回内置默认值。默认仍逐次审批，因此无人值守运行需要显式声明风险：

| 参数 | 说明 |
|---|---|
| `--yes` | 自动批准所有工具调用 |
| `--ack-unsafe-auto-approve` | 与 `--yes` 联用，确认已知“非沙箱”风险；缺少它时 `--yes` 直接报 `CONFIG_ERROR` |
| `--command-policy PATH` | `command-policy-v1` 精确 command/cwd allowlist；`--yes` 必须提供且不能为空 |

`--report-out` 写出 `run-report-v1` 文档：run 状态、停止原因、错误类别、模型标识、主/压缩请求数与 attempts、provider usage 分量、工具统计（参数只以 sha256 哈希出现）、压缩次数与估算误差、各阶段耗时。报告不含 prompt 原文、工具参数原文、命令输出、绝对路径、凭据或 API endpoint。退出码：run 以 `COMPLETED` 结束为 0，否则为 1；配置错误为 2。

### 工作区指令（AGENTS.md）与技能（.agents/skills/）

网页与 headless 共用同一套“运行起点工作区扫描”，对每个 Session 的工作区生效：

- **AGENTS.md**：工作区根目录的 `AGENTS.md`（也接受小写 `agents.md`，其它大小写不识别）会在**每次 run 开始时**读取，作为系统提示的一部分注入（`## Workspace instructions (AGENTS.md)` 小节）。文件在两次 run 之间修改，下一次 run 生效。读取走与工具相同的 workspace 路径边界；超过 16 KiB 截断并附加标记；缺失或不可读时静默忽略。该小节属于 §7.2 意义上的不可压缩内容：上下文压缩永远不会摘要或裁掉它。
- **技能（skills）**：`<workspace>/.agents/skills/<skill-name>/SKILL.md` 声明一个技能。技能**不会**自动进入上下文；每次 run 开始时扫描一次（只扫一层目录），把技能索引（名称 + 描述）注入系统提示的 `## Available skills` 小节，模型按需通过第 4 个模型可见工具 `skill` 加载。

`SKILL.md` 格式：YAML frontmatter 之外只需 `key: value` 行，`---` 之间必须包含 `name`（小写字母/数字/连字符，且等于目录名）与 `description`（1-1024 字符）；`---` 之后是正文。目录内可捆绑任意伴生文件（脚本、参考文档）。示例：

```markdown
---
name: git-helper
description: Guide routine Git operations with staged, explained commands.
---

1. Inspect the current branch state first.
2. Propose one command at a time and explain what it changes.
```

`skill` 工具参数：`mode` 为 `"read"`（默认，需 `name`）或 `"list"`。`read` 返回完整 SKILL.md 正文与伴生文件清单（相对路径 + 字节数），模型随后可用 `read_file` 自行读取伴生文件（它们本就在工作区内）；`list` 返回索引（名称、描述、文件数）。未知技能名返回 `UNKNOWN_SKILL` 工具错误，模型可自行纠正。`skill` 是只读的 workspace 内操作，**不需要审批**。frontmatter 无效或缺失的技能会被跳过并记录 `skill.invalid` 诊断事件，不会导致 run 失败；技能目录的修改在下一次 run 生效。

## 6. Makefile 入口

`Makefile` 只编排项目自身命令，不绕过 `uv`、`npm` 或正式 CLI；所有目标声明为 `.PHONY`，任一子命令失败立即返回非零退出码。Makefile **不提供** `clean`、`distclean` 等删除目标。

| 命令 | 行为 |
|---|---|
| `make help` | 列出可用目标和可传入变量，默认目标 |
| `make install` | 创建 Python 3.12 环境、安装后端与前端依赖及 Playwright Chromium |
| `make build` | `npm --prefix web run build`，生成 FastAPI 托管的 `web/dist` |
| `make start` | 依赖 `build`，启动生产形态的单进程服务 |
| `make dev-api` | 只启动 FastAPI 开发后端 |
| `make dev-web` | 只启动 Vite 开发服务器 |
| `make eval-run` | 跑一轮测评 campaign（12 个公开任务；真实模型，需要 API key 环境变量） |
| `make eval-judge` | 同 `eval-run`，并在每次运行后用 LLM 裁判打三项分（需要 API key） |
| `make eval-history` | 在终端列出历史 campaign（成功率与裁判均分） |
| `make eval-web` | 依赖 `build`，启动服务并浏览测评结果网页（只读，可看历史数据） |
| `make test-backend` | 运行后端单元与集成测试（`--ignore=tests/live`） |
| `make test-frontend` | 运行前端组件测试 |
| `make test` | 顺序执行 `test-backend` 和 `test-frontend`，默认不访问网络 |
| `make test-e2e` | 运行基于 `ScriptedModel` 的 Playwright E2E |
| `make lint` | 后端 Ruff 检查（`src tests scripts`）与前端 lint |
| `make format` | 后端 Ruff formatter（`src tests scripts`）与前端 format |
| `make check` | 依次执行 lint、默认测试、E2E 和生产构建 |

启动参数通过 Make 变量传递：`CONFIG` 默认 `config.toml`，`ARGS` 原样追加到正式 CLI，因此参数校验仍由 CLI 完成。测评目标使用 `EVAL_OUT`（结果目录，默认 `evaluation-results/`，已被 gitignore）、`EVAL_REPEATS`（每任务重复次数，默认 `1`）与 `MANIFEST`（任务清单，默认公开任务集）。

测评的典型工作流（需要模型凭据在环境变量中）：

```bash
make eval-judge                                # 跑 12 任务 × 1 次 + LLM 裁判
make eval-history                              # 终端看历史汇总
make eval-web                                  # 网页浏览结果（#/evaluations）
```

## 7. 架构

事件驱动的模块化单体：一个 FastAPI 进程、一个 React 应用、一个 SQLite 数据库。没有 Redis、消息队列或进程间 RPC。

```text
React Workbench
    │  REST：bootstrap、会话列表、快照、目录浏览
    │  WebSocket：发起 run、审批、停止与实时事件
    ▼
FastAPI API / WS Gateway
    ▼
Run Coordinator ── Session Service ── Event Publisher
    ▼
Agent Loop
    ├── Context Builder / Compactor
    ├── Anthropic-compatible Messages Model Adapter
    ├── Approval Gate
    ├── Tool Registry（read_file / write_file / run_command）
    └── SQLite Session Store
```

- **Agent Loop**（`src/coding_agent/runtime/loop.py`）：每轮一次模型调用，受 `agent.max_rounds` 轮次上限约束；重复工具指纹超过 `agent.doom_loop_threshold` 判为 `DOOM_LOOP`，工具参数错误重试超过 `agent.tool_argument_retries` 同样终止。停止原因是显式枚举：`COMPLETED`、`USER_STOP`、`MAX_ROUNDS`、`DOOM_LOOP`、`EMPTY_RESPONSE`、`OUTPUT_TRUNCATED`、`INCOMPLETE_TOOL_CALL`、`AUTH_ERROR`、`CONFIG_ERROR` 等。
- **Context Builder / Compactor**（`src/coding_agent/context/`）：按 `context_window - max_output_tokens - safety_margin_tokens` 计算预算；先做确定性 tool-output 裁剪，仍超阈值时发起一次**同步** LLM 摘要。压缩只改变发给模型的临时视图，所有 user 原文逐字保留，SQLite 中的原始历史不被删除或改写。
- **Model Adapter**（`src/coding_agent/model/`）：把厂商无关的语义对象翻译成 Anthropic content blocks，聚合 Messages SSE 增量为完整 assistant 轮次，并校验 tool call 的结构与参数；协议异常映射为分类错误。Agent Core 不接触 wire payload，Model Adapter 不访问 workspace。
- **Approval Gate**（`src/coding_agent/runtime/approval.py`）：见 §8。
- **四个本地工具**（`src/coding_agent/tools/`）：`read_file` 读工作区 UTF-8 文本（按行/字节上限截断）、`write_file` 写入或替换、`run_command` 执行非交互命令、`skill` 按需读取工作区技能（见 §5）。所有路径先归一化到 workspace 边界内，越界返回普通 tool error 而不是崩溃。
- **SQLite 是唯一权威状态**：会话、canonical transcript、model requests、tool executions、上下文快照、durable events 和 client command receipts 全部落库，不与 JSONL 双写。事件带单调递增序号，浏览器重连后按序号补发。

## 8. 审批模型与安全边界

- `read_file` 自动执行；`write_file` 和 `run_command` **默认逐次审批**。
- 审批以后端 `prepare` 阶段冻结的参数为准：前端只能对某个 `tool_call_id` 回答“批准/拒绝”，**不能提交或替换参数**。命令的 cwd 在执行前二次校验，若审批后发生变化则以 `COMMAND_CWD_CHANGED` 失败。
- **`--yes` 是受信任模式**：它跳过全部审批，模型提出的写入和命令会**无人确认地直接执行**。只在你完全信任任务、工作目录和模型输出时使用；headless 下还必须同时给出 `--ack-unsafe-auto-approve` 和非空 `--command-policy`。`--yes` 不会绕过同源与 token 校验。
- **`run_command` 不是沙箱。** 被批准的命令由本机后端在一个新的 POSIX 进程组中执行，**使用当前操作系统用户的全部权限**，可以读写该用户能访问的任何文件、访问网络。项目只提供有限的减害措施：命令 cwd 限制在 workspace 内、stdin 为 `/dev/null`、独立进程组便于超时后整组终止、输出字节上限、隔离的临时 `HOME`、默认清空环境变量（只透传 `PATH`、locale、临时目录，以及 `tools.pass_env` 显式列出的变量），并且**始终从子进程环境中移除模型 API key 变量**。这些都不是安全隔离，不能防御恶意命令。请只在你愿意承担后果的工作目录中运行。
- 生产模式只接受同源请求：校验 `Host` 为 `127.0.0.1`/`localhost` 与实际端口，WebSocket 校验 `Origin`；所有改变状态的命令还需携带启动时生成、仅通过同源 bootstrap 暴露的随机 token。服务重启后旧 token 失效，前端重新 bootstrap 并按序号恢复。
- 服务重启后采取**保守恢复**：已开始但结果未落库的副作用不重放，中断的 run 标记为 `INTERRUPTED`，历史仍可完整读取。
- 公开仓库不包含凭据、数据库、日志、构建产物、内部路径或私有素材；`config.toml`、`.venv/`、`node_modules/`、`web/dist/`、`*.db`、`*.log`、测试报告均被忽略。

## 9. 协议范围

本项目只实现**一套**协议：**Anthropic-compatible Messages API + Messages SSE + 客户端 tool use**。任何符合这三条语义的服务，只要改 `config.toml` 的 `[model]` 段（`base_url`、`model`、`api_key_env`）就能切换，无需改代码。

不支持的第二协议：不支持 OpenAI Chat Completions、Responses API 或其它非 Messages 语义的接口；不支持 Claude Agent SDK、LangChain、LlamaIndex、AutoGen、CrewAI、MCP SDK 等 Agent 框架/SDK；不支持服务端托管的执行或文件能力（server tools、code interpreter、files API）；不使用 extended thinking 与 beta headers。首版也不做多 run 并发、多 Agent/子 Agent、RAG/向量库/代码索引、独立的 search/grep/edit/patch 工具、PTY 与交互式 stdin、远程部署与账号系统。

推理输出保持中性：请求不携带 `thinking` 字段，是否开启推理由服务端默认决定（例如阿里云百炼的 Qwen 默认思考，Anthropic 官方默认不思考）。兼容服务返回 `thinking` block 时，后端按 block 聚合文本、随消息持久化，并以瞬态事件推送到 UI 折叠展示；`signature_delta` 直接丢弃，推理内容从不回传给模型，也不计入上下文估算。`[model]` 的 `base_url` 可指向任何 Anthropic-compatible 端点，例如 Anthropic 官方 `https://api.anthropic.com` 或阿里云百炼 Token Plan 入口 `https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic`（模型名与凭据环境变量按所用服务调整）。

`stream = false` 的非流式兼容、`pause_turn` 自动 continuation 属于后续增量，当前配置校验会直接拒绝 `stream = false`，不留“可配置但不可用”的分支。

## 10. 测试

[![CI](https://github.com/hanhaiqingchuan/my-coding-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/hanhaiqingchuan/my-coding-agent/actions/workflows/ci.yml)

默认测试全部离线、确定性，不访问真实模型：

```bash
make test        # 后端 pytest + 前端 vitest
make test-e2e    # Playwright E2E，使用 ScriptedModel
make check       # lint + test + test-e2e + build
```

后端也可直接运行：

```bash
uv run --python 3.12 pytest -q
uv run --python 3.12 ruff check src tests scripts
uv run --python 3.12 ruff format --check src tests scripts
```

### 真实模型冒烟（默认跳过）

`tests/live/` 是唯一允许访问模型服务的测试目录，**默认被跳过**，也不包含在 `make test` / `make check` 中。只有显式设置开关才会真实调用：

```bash
export ANTHROPIC_API_KEY="<你的密钥>"
export LIVE_MODEL="<你的模型名>"           # 必填；未设置则跳过
export LIVE_BASE_URL="https://api.anthropic.com"   # 可选，默认值即此
export LIVE_API_KEY_ENV="ANTHROPIC_API_KEY"        # 可选，改用其它变量名存放凭据
RUN_LIVE_TESTS=1 uv run --python 3.12 pytest tests/live -q
```

覆盖四个场景：新建小文件并运行测试；修改已有函数并验证原测试仍通过；工具失败后依据错误结果自纠；通过第二个 Anthropic-compatible Messages 服务或模型配置完成同类任务（需额外设置 `LIVE_ALT_MODEL`，可选 `LIVE_ALT_BASE_URL`、`LIVE_ALT_API_KEY_ENV`；未设置则跳过该场景）。

凭据只从环境变量读取，绝不写入 fixture、日志或报告。这些测试只断言可确定的结构性事实（run 状态、停止原因、工具调用统计、我们自己在 workspace 外运行的 oracle 结果），不断言模型措辞；每次运行记录成功与否、rounds、tool calls、stop reason、usage、重试与压缩次数。设置 `LIVE_REPORT_DIR` 可把脱敏后的记录写到仓库外的目录，`LIVE_TIMEOUT_SECONDS` 可调整单次超时（默认 300 秒）。

不设开关时整个目录被跳过，可自行验证：

```bash
uv run --python 3.12 pytest tests/live -q     # 全部 skipped，退出码 0
```

## 11. 评测 harness

`evaluation/` 与 `src/coding_agent/evaluation/` 提供确定性评测框架：它通过正式的 headless CLI 驱动产品，不含第二套 Agent Loop，也不引入外部 Agent 框架。

```bash
# 校验 manifest，并用 baseline/gold/error 变体证明每个任务可评分
uv run --python 3.12 coding-agent-eval validate \
  --manifest evaluation/tasks/public/manifest.toml

# 先 dry-run：只打印任务数、请求上限、工作目录和输出位置，不调用模型
uv run --python 3.12 coding-agent-eval run \
  --manifest evaluation/tasks/public/manifest.toml \
  --repeats 1 --serial --out /path/outside/this/repo --dry-run

# 真实运行（需要 config.toml 与 ANTHROPIC_API_KEY）
uv run --python 3.12 coding-agent-eval run \
  --manifest evaluation/tasks/public/manifest.toml \
  --config config.toml --repeats 1 --serial --out /path/outside/this/repo

# 聚合 campaign 的运行记录，写出 summary.json、summary.csv 与 report.md
uv run --python 3.12 coding-agent-eval summarize --input /path/outside/this/repo
```

`run` 只写它拥有的不可变记录：`runs.jsonl` 与每次 repeat 的 `run.json`；`summarize` 负责聚合，默认写到 `<campaign>/reports/`（也可用 `--out` 指定目录）。两条命令都不覆盖已存在的产物，因此再次聚合到同一目录会直接报错而不是改写已发布结果。

每次 run 使用**全新临时 workspace 和独立 `--data-dir`**，不复用用户的默认数据库；隐藏 oracle 在 workspace 外运行，模型无法影响自己的评分。原始 transcript 和完整结果按约定写到仓库外（`--out` 指向仓库外目录），公开仓库只保留框架、schema、四个可再分发的公开任务和 `evaluation/examples/` 中的**脱敏**示例结果。默认导出不含 prompt 原文、工具参数、命令输出和绝对路径——工具参数只以 `args_hash` 出现。细节见 [`evaluation/README.md`](evaluation/README.md)。

### 评测结果网页

评测结果有专门的只读网页视图：左栏在 Sessions 旁多出 Evaluations 入口，点击进入结果工作台——campaign 列表（时间窗口、任务数、strict success 率、judge 均分、模型身份）→ 单个 campaign 的每任务运行行（轮次、工具调用、输入/输出 token、耗时、strict_success/artifact 徽标、judge 三项小分）→ 单 run 详情（run-v1 事实 + judge 三项分数，judge 理由默认折叠）。网页只“看”：不能从网页发起评测、编辑或删除结果；运行仍走上面的 CLI。

```bash
# 把 serve 指向你的结果根目录（含多个 campaign 子目录，与 history --results 相同口径）
npm --prefix web run build
uv run --python 3.12 coding-agent serve \
  --config config.toml \
  --eval-results /path/to/evaluation-results

# 浏览器打开 http://127.0.0.1:8000，左栏切到 Evaluations
```

`--eval-results` 缺省时读取 `<data-dir>/evaluation-results`（即默认 `./.coding-agent/evaluation-results`）。结果根目录不存在或某条记录损坏时，网页显示空态或降级提示，不会报 500；页面数据来自与 `history` 命令相同的只读扫描，响应中不含任何绝对路径或凭据。

## 12. 发布前审计

公开交付前运行两条显式命令（有意不接入 Makefile，避免扩大已确认的 Makefile 接口）：

```bash
uv run --python 3.12 scripts/audit_public.py --repo .
uv run --python 3.12 scripts/check_readme_txt.py README.txt
```

`audit_public.py` 扫描 tracked 文件（加 `--history` 时扫描完整 Git 历史）中的凭据模式、禁止路径、内部痕迹和协议残留，只报告规则 ID、文件与行号/commit，从不回显命中原文。退出码：`0` 干净、`1` 工作树有命中、`2` 仅历史有命中、`3` 用法或 Git 错误。`check_readme_txt.py` 按 Unicode code point 校验 `README.txt` 正文不超过 1000 字符，并检查仓库地址、最短运行方法、功能摘要和凭据模式。

## 13. 目录结构

```text
my-coding-agent/
├── Makefile                     统一入口
├── config.example.toml          配置模板
├── pyproject.toml               依赖与 console scripts
├── doc/                         设计方案与实施计划
├── src/coding_agent/
│   ├── cli.py  main.py          argparse 入口与 composition root
│   ├── config.py                配置加载与校验
│   ├── api/                     FastAPI REST/WebSocket
│   ├── core/                    状态机、事件、语义模型、取消
│   ├── model/                   Messages 适配器、SSE 聚合、重试
│   ├── context/                 预算、裁剪、同步摘要压缩
│   ├── runtime/                 Agent Loop、协调器、审批、事件、指标
│   ├── storage/                 SQLite schema 与访问
│   ├── tools/                   read_file / write_file / run_command
│   └── evaluation/              评测 CLI、manifest、runner、报告
├── web/                         React + TypeScript + Vite 前端
├── tests/                       unit / integration / evaluation / live
├── e2e/                         Playwright 场景
├── evaluation/                  schema、公开任务、脱敏示例
└── scripts/                     发布审计与 README 门禁
```
