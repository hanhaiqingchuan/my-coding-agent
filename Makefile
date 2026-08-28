.DEFAULT_GOAL := help

CONFIG ?= config.toml
ARGS ?=

.PHONY: help install build start dev-api dev-web test-backend test-frontend test test-e2e lint format check

help:
	@echo "My Coding Agent commands"
	@echo "  install        Create the Python 3.12 environment and install all dependencies"
	@echo "  build          Build the production web application"
	@echo "  start          Build and start the production service"
	@echo "  dev-api        Start only the API development server"
	@echo "  dev-web        Start only the Vite development server"
	@echo "  test-backend   Run offline Python tests (excluding tests/live)"
	@echo "  test-frontend  Run frontend component tests"
	@echo "  test           Run offline backend and frontend tests"
	@echo "  test-e2e       Run Playwright E2E tests using the scripted model"
	@echo "  lint           Run Ruff and frontend lint checks"
	@echo "  format         Format Python and frontend source files"
	@echo "  check          Run lint, tests, E2E, and a production build"
	@echo "Variables: CONFIG=config.toml ARGS='--workspace /path/to/project --open'"

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
