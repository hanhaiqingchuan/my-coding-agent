Make Code Great Again

公开仓库：https://github.com/hanhaiqingchuan/my-coding-agent

运行方式（macOS/Linux环境，需 Python 3.12 与 Node）：
  make install
  cp config.example.toml config.toml      # 改 [model] 的 model 与 base_url 和其他待填配置项
  export ANTHROPIC_API_KEY="<你的密钥>"
  make start ARGS='--workspace /path/to/project --open'
浏览器打开 http://127.0.0.1:8000，选目录后即可多轮对话。密钥只从环境变量读取，配置里只写变量名

项目自行实现：
1. Anthropic-compatible Messages 流式解析与 tool call 结构校验，未使用现成 Agent 框架
2. Agent Loop：轮次上限、重复调用防循环、参数错误重试，停止原因显式枚举等
3. 上下文管理：记录用户请求、模型轨迹，压缩时采用确定性 tool 输出裁剪和 LLM 摘要；压缩过程中保证原始历史不被改写，并保留用户关键决策。工作区 AGENTS.md 自动注入，.agents/skills/ 技能按需加载
4. 实现 read_file / write_file / run_command / skill 四个本地工具；写入与命令需要人工批准或拒绝，或使用模型自动审批
5. 完成了 Stop、限流与网络重试、刷新重连、服务重启等常见错误处理
6. 以 SQLite 作为会话信息事实源，可复原完整历史与带序号事件
7. 确定性过程指标测评：在隔离 workspace 与独立 --data-dir 中运行公开任务，产出脱敏报告
   借助测评指标分析agent实现瑕疵，对工具报错信息、系统提示词、工作流程和工程规范等内容进行多轮迭代优化，在评测指标中取得较大进步
8. 开发过程中遵循良好工程规范，使用Superpowers skill及TDD、SDD工作流程指导agent完成项目，并利用github action进行CI测试；使用确定性过程指标测评作为项目迭代依据
