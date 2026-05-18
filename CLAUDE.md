# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

An educational AI hedge fund (no real trades). It contains **three largely independent codebases** in one setuptools project (see `[tool.setuptools.packages.find]` in `pyproject.toml`):

- `src/` — **v1, the production system.** LLM "investor persona" agents orchestrated with LangGraph. This is what the CLI and web app run.
- `app/` — FastAPI backend + React/Vite frontend that wrap the `src/` engine in a web UI.
- `v2/` — a ground-up **quantitative** rebuild (methodology over personality). **Work in progress, NOT integrated** into `src/` or `app/`. Read `v2/README.md` before touching it.

Treat `src/` and `v2/` as separate worlds — they do not import each other.

## Commands

Standard PEP 621 / setuptools project (no Poetry). Uses a local `.venv`,
matching the convention of the sibling apps under `~/quantai-trading/`.

```bash
# One-time setup
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"                       # install incl. dev extras
.venv/bin/pip install -e ~/quantai-trading/quantai-market-data  # shared data layer (prices + fundamentals)
# Then set QUANTAI_MARKETDATA_DB_URL in .env (shared Postgres DSN).

# Run the v1 hedge fund (CLI)
.venv/bin/python src/main.py --ticker AAPL,MSFT,NVDA
.venv/bin/python src/main.py --ticker AAPL --ollama --start-date 2024-01-01 --end-date 2024-03-01

# Backtester (also exposed as the `backtester` console script -> src.backtesting.cli:main)
.venv/bin/python src/backtester.py --ticker AAPL,MSFT,NVDA
.venv/bin/backtester --ticker AAPL,MSFT,NVDA

# Web app — primary path is the quantai orchestrator
quantai start hedgefund     # backend :8006 (/docs), frontend :5177; quantai status / stop hedgefund
cd app && ./run.sh          # alternative standalone runner (same ports)

# Tests
.venv/bin/pytest                                            # all tests under tests/
.venv/bin/pytest tests/backtesting/test_portfolio.py        # one file
.venv/bin/pytest tests/backtesting/test_portfolio.py::test_name -q   # one test
.venv/bin/pytest v2/event_study/test_event_study.py         # v2 tests live BESIDE the code, not in tests/

# Format / lint
.venv/bin/black .         # NOTE: line-length is 420 (intentional — do not "fix" long lines)
.venv/bin/isort .
.venv/bin/flake8

# Release (CalVer YYYY.M.D; bumps pyproject, tags, creates GitHub release)
bash scripts/release.sh [version]
```

`tests/` covers only the `src/` backtesting engine. `v2/` keeps `test_*.py` next to the modules they test.

## v1 architecture (`src/`)

The system is a **LangGraph `StateGraph`** built in `create_workflow()` (`src/main.py`):

```
start_node ─┬─> analyst agent ─┐
            ├─> analyst agent ─┼─> risk_management_agent ─> portfolio_manager ─> END
            └─> analyst agent ─┘
```

All selected analysts run in parallel from `start_node`, fan into `risk_management_agent` (sets position limits), then `portfolio_manager` (final buy/sell/hold decisions). Risk and portfolio nodes are always present; analysts are selectable.

Key concepts:

- **State** (`src/graph/state.py`): `AgentState` is a TypedDict with `messages`, `data`, `metadata`. `data` and `metadata` use merge reducers, so parallel analyst nodes each write into shared `data["analyst_signals"]` without clobbering each other.
- **Agent registry** (`src/utils/analysts.py`): `ANALYST_CONFIG` is the _single source of truth_. To add an analyst: create `src/agents/<name>.py` exposing a `def <name>_agent(state, agent_id=...)` that returns updated state, then add an entry to `ANALYST_CONFIG`. Node names, CLI menus, and API listings are all derived from this dict — do not wire agents into the graph manually elsewhere.
- **Agent shape**: each agent fetches data, builds a Pydantic signal model (`signal`/`confidence`/`reasoning`), and calls the LLM. See `src/agents/warren_buffett.py` as the canonical example.
- **Data layer**: `src/tools/api.py` keeps stable signatures but delegates to `src/tools/providers/` — a provider-routed layer (`router.py` + `config.py`) that picks a source per data type from `DATA_PROVIDER`/`DATA_PROVIDER_<TYPE>` env, with automatic Financial Datasets fallback. Providers: `financialdatasets` (baseline/fallback), `internal` (shared `quantai-market-data` Postgres + Polygon + the new `quantai_market_data.fundamentals` submodule), `polygon` (news, financials vX), `sec_edgar` (Form 4 insider, companyfacts), `metrics` (ratios computed locally via `_derive.py`). In-memory cache `src/data/cache.py`; Pydantic contracts in `src/data/models.py` are unchanged. Migration plan + status: see the `fd-migration-state` memory; validate with `scripts/data_parity.py` / `scripts/validate_prices.py` before flipping a type's default off `fd`.
- **LLM layer**: all model calls go through `src/utils/llm.py` `call_llm`. Available models are declared in `src/llm/api_models.json` / `ollama_models.json` and loaded via `src/llm/models.py`.
- **API keys**: resolve via `src/utils/api_key.py` `get_api_key_from_state` — the web app passes keys through `AgentState`, so do not read `os.environ` directly in agents. At least one LLM provider key plus `FINANCIAL_DATASETS_API_KEY` is required (`.env`, copied from `.env.example`).

## Web app (`app/`)

- `app/backend/` — FastAPI (`main.py`), routes in `routes/`, business logic in `services/`. `services/graph.py` constructs the same LangGraph workflow for API/streaming use; `agent_service.py` / `backtest_service.py` drive runs. Persistence via SQLAlchemy + Alembic migrations (`app/backend/database/`, `app/backend/alembic/`).
- `app/frontend/` — React + Vite (dev server on :5177, talks to backend on :8006). Backend URL is centralized in `app/frontend/src/lib/api-base.ts` (override via `VITE_API_URL` in `app/frontend/.env`); CORS origins are set in `app/backend/main.py`.

## v2 quant pipeline (`v2/`)

Pipeline: `data → signals → features → portfolio → risk → pipeline (execution)`. Core data contracts in `v2/models.py`; signal interface is the `BaseSignal` ABC in `v2/signals/base.py` (output constrained to `[-1, +1]`). Emphasis on point-in-time data, transaction-cost-aware backtesting, and CPCV/PBO validation. It is not callable from the v1 system yet.
