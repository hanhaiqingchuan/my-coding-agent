My Coding Agent — 面向本地代码仓库的 coding agent

公开仓库：https://github.com/hanhaiqingchuan/my-coding-agent

最短运行方式（macOS/Linux，需 Python 3.12 与 Node）：
  make install
  cp config.example.toml config.toml      # 改 [model] 的 model 与 base_url
  export ANTHROPIC_API_KEY="<你的密钥>"
  make start ARGS='--workspace /path/to/project --open'
浏览器打开 http://127.0.0.1:8000，选目录后即可多轮对话。密钥只从环境变量读取，配置里只写变量名。

自研实现的部分：
1. Anthropic-compatible Messages 流式解析与 tool call 结构校验，只用官方客户端做 HTTP/SSE，无 Agent 框架。
2. Agent Loop：轮次上限、重复调用防循环、参数错误重试，停止原因是显式枚举。
3. 上下文预算：确定性 tool 输出裁剪 + 同步 LLM 摘要；user 原文逐字保留，原始历史不被改写。
4. read_file / write_file / run_command 三个本地工具；写入与命令逐次审批，审批参数在后端冻结，前端只能批准或拒绝。
5. Stop、限流与网络重试、刷新重连、服务重启后不重放已完成副作用。
6. SQLite 是唯一事实源，完整历史与带序号事件可恢复。
7. 确定性评测 harness：在隔离 workspace 与独立 --data-dir 中运行公开任务，产出脱敏报告。

注意：run_command 不是沙箱，已批准的命令以当前系统用户权限执行；--yes 跳过审批，仅用于受信任的自动化。
本版只实现 Anthropic-compatible Messages API 加客户端 tool use，不支持 OpenAI Chat Completions 等第二协议。
