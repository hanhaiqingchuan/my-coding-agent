# My Coding Agent — unified development entry point.
#
# All targets delegate to the real CLIs (`uv run` / `npm`); the Makefile only
# orchestrates. API keys are ALWAYS environment variables (never Make vars).
#
# ── 常用命令速查 ────────────────────────────────────────────────────────────
#
# 安装（一次性）:
#   make install                 创建 .venv(Python 3.12) + 前端依赖 + Chromium
#
# 运行（网页工作台）:
#   make start                   构建前端并启动生产服务，默认 http://127.0.0.1:8000
#     CONFIG=<path>              配置文件，默认 ./config.toml
#     ARGS='--workspace DIR --open'
#                                传给 serve 的参数：初始 workspace、自动开浏览器、
#                                --yes 受信任模式(跳过审批，仍全审计)、--port N
#   make dev-api / make dev-web  开发模式双终端（Vite 热更新，/api 与 WS 代理）
#
# 测评（需要 DASHSCOPE_API_KEY 等真实凭据，走真实模型）:
#   make eval-run                跑一轮 campaign（默认 12 个公开任务 × 1 次）
#     EVAL_OUT=<dir>             结果目录（默认 evaluation-results/，勿入 git）
#     EVAL_REPEATS=<n>           每任务重复次数，默认 1
#     CONFIG=<path>              模型配置，默认 ./config.toml
#   make eval-judge              同 eval-run，并在每次运行后用 LLM 裁判打
#                                task_completion/process_quality/communication 三项分
#   make eval-history            在终端列出历史 campaign（含成功率与裁判均分）
#   make eval-web                启动服务并浏览测评结果网页（左栏 Evaluations 或
#                                #/evaluations 深链）：campaign 列表 → 任务指标 →
#                                单次运行 + 裁判理由；只读，可看任意历史数据
#     EVAL_OUT=<dir>             与 eval-run 的输出目录一致
#
# 测试（默认全部离线，不访问模型网络）:
#   make test / test-backend / test-frontend / test-e2e / lint / format / check
#
# ── 完整目标列表见 make help ────────────────────────────────────────────────

.DEFAULT_GOAL := help

CONFIG ?= config.toml
ARGS ?=
EVAL_OUT ?= evaluation-results
EVAL_REPEATS ?= 1
MANIFEST ?= evaluation/tasks/public/manifest.toml

.PHONY: help install build start dev-api dev-web test-backend test-frontend test test-e2e lint format check eval-run eval-judge eval-history eval-web

help:
	@echo "My Coding Agent commands"
	@echo ""
	@echo "Setup"
	@echo "  install        Create the Python 3.12 environment and install all dependencies"
	@echo "  build          Build the production web application"
	@echo ""
	@echo "Run"
	@echo "  start          Build and start the production service (web workbench)"
	@echo "  dev-api        Start only the API development server"
	@echo "  dev-web        Start only the Vite development server"
	@echo ""
	@echo "Evaluation (real model, needs the API key env var)"
	@echo "  eval-run       Run a campaign over the public task set (EVAL_OUT/EVAL_REPEATS/CONFIG)"
	@echo "  eval-judge     eval-run plus the LLM judge scoring every finished run"
	@echo "  eval-history   List past campaigns with success rates and judge means"
	@echo "  eval-web       Start the service and browse evaluation results (EVAL_OUT)"
	@echo ""
	@echo "Test (offline by default)"
	@echo "  test-backend   Run offline Python tests (excluding tests/live)"
	@echo "  test-frontend  Run frontend component tests"
	@echo "  test           Run offline backend and frontend tests"
	@echo "  test-e2e       Run Playwright E2E tests using the scripted model"
	@echo "  lint           Run Ruff and frontend lint checks"
	@echo "  format         Format Python and frontend source files"
	@echo "  check          Run lint, tests, E2E, and a production build"
	@echo ""
	@echo "Variables"
	@echo "  CONFIG=$(CONFIG)  ARGS='--workspace /path/to/project --open'"
	@echo "  EVAL_OUT=$(EVAL_OUT)  EVAL_REPEATS=$(EVAL_REPEATS)  MANIFEST=$(MANIFEST)"

install:
	@test -d .venv || uv venv .venv --python 3.12
	uv sync --frozen --python 3.12 --all-groups
	npm --prefix web ci
	npm --prefix web exec -- playwright install chromium

build:
	npm --prefix web run build

start: build
	uv run --python 3.12 coding-agent serve --config "$(CONFIG)" $(ARGS)

dev-api:
	uv run --python 3.12 coding-agent serve --config "$(CONFIG)" $(ARGS)

dev-web:
	npm --prefix web run dev

eval-run:
	uv run --python 3.12 coding-agent-eval run \
		--manifest "$(MANIFEST)" \
		--config "$(CONFIG)" \
		--repeats "$(EVAL_REPEATS)" \
		--serial \
		--out "$(EVAL_OUT)"

eval-judge:
	uv run --python 3.12 coding-agent-eval run \
		--manifest "$(MANIFEST)" \
		--config "$(CONFIG)" \
		--repeats "$(EVAL_REPEATS)" \
		--serial \
		--judge \
		--out "$(EVAL_OUT)"

eval-history:
	uv run --python 3.12 coding-agent-eval history --results "$(EVAL_OUT)"

eval-web: build
	uv run --python 3.12 coding-agent serve \
		--config "$(CONFIG)" \
		--eval-results "$(EVAL_OUT)" \
		$(ARGS)

test-backend:
	uv run --python 3.12 pytest --ignore=tests/live

test-frontend:
	npm --prefix web run test

test: test-backend test-frontend

test-e2e:
	npm --prefix web run test:e2e

lint:
	uv run --python 3.12 ruff check src tests scripts
	npm --prefix web run lint

format:
	uv run --python 3.12 ruff format src tests scripts
	npm --prefix web run format

check: lint test test-e2e build
