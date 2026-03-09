---
title: PrimoGreedy Agent
emoji: 💸
colorFrom: green
colorTo: blue
sdk: docker
pinned: false
app_port: 7860
---

# PrimoGreedy Agent

**PrimoGreedy** is an automated, AI-driven financial analysis agent designed to hunt, filter, and evaluate Micro-Cap and Small-Cap stocks. It acts as a ruthless "Logic Firewall," aggressively rejecting high-debt, cash-burning, and overvalued companies before deploying a multi-agent LLM pipeline to write highly structured fundamental investment memos — and optionally execute paper trades via Alpaca.

---

## Core Architecture (The LangGraph Engine)

The system is built on **LangGraph** following modern best practices (partial state updates, `Command` routing, `Send` parallel fan-out, checkpointing, `RetryPolicy`, and multi-agent subgraphs).

### Hunter Pipeline (`src/agent.py` / `src/whale_hunter.py`)

```
START --> initial_routing --> [chat] --> END
                          \-> [scout] --> [gatekeeper] --Command--> [analyst] --> END
                                              \--Command--> [scout]  (retry)
```

1. **Scout Node** — Discovers candidates via yFinance screener + Brave Search trending, scores and ranks them, and pops the best unseen ticker.
2. **Gatekeeper Node** — Strict quantitative firewall using the `Command` pattern for routing:
   - Market Cap: $5M -- $500M
   - Share Price: under $30.00
   - Zombie Filter: rejects unprofitable companies with < 6 months cash runway
   - Routes directly to `analyst` (PASS / retries exhausted) or back to `scout` (FAIL) via `Command`.
3. **Analyst Node** — Two modes controlled by `USE_DEBATE` env var:
   - **Single-LLM** (default): Senior Broker analysis via OpenRouter (6-model fallback chain) with structured `InvestmentVerdict` output.
   - **Multi-Agent Debate** (`USE_DEBATE=true`): Three-agent Investment Committee subgraph (Pitcher → Skeptic → Judge) that produces a hallucination-resistant verdict.

   Both modes fetch **SEC EDGAR** 10-K/10-Q filings (US equities), call Finnhub tools for deep fundamentals, and compute **Kelly Criterion position sizing**.

### Workflow Pipeline (`src/workflows/workflow.py`)

```
START --> [data_collection] --> [technical_analysis] --> [news_intelligence] --> [portfolio_manager] --> END
```

A linear 4-node pipeline for deep single-ticker analysis (used by `main.py` CLI).

### Multi-Agent Debate (`src/agents/debate.py`)

When `USE_DEBATE=true`, the analyst node runs a 3-agent LangGraph subgraph:

```
START --> [pitcher (Gemma)] --> [skeptic (Mistral)] --> [judge (Nemotron)] --> END
```

1. **The Pitcher** — Writes the strongest bullish thesis using only provided data.
2. **The Skeptic** — Challenges the bull case, flagging any fabricated claims.
3. **The Judge** — Synthesises the debate into a structured `InvestmentVerdict`, downgrading if fabrications were found.

Models are configurable via `DEBATE_PITCHER_MODEL`, `DEBATE_SKEPTIC_MODEL`, `DEBATE_JUDGE_MODEL` env vars.

### Parallel Region Orchestrator (`src/whale_hunter.py`)

The daily cron dispatches all 4 markets (USA, UK, Canada, Australia) **in parallel** via the LangGraph `Send` API:

```
START --> dispatch_regions --> [hunt_region: USA]       \
                          \-> [hunt_region: UK]         |-- region_results --> END
                          \-> [hunt_region: Canada]     |
                          \-> [hunt_region: Australia]  /
```

Each `hunt_region` invokes the full per-region subgraph (scout -> gatekeeper -> analyst -> email).

Supports **catalyst-triggered single-ticker mode** via `CATALYST_TICKER` env var (used by `repository_dispatch` from the VPS polling daemon).

---

## LangGraph Features Used

| Feature | Where | Purpose |
|---------|-------|---------|
| Partial state updates | All agent nodes | Nodes return `dict` with only changed keys |
| `Annotated` reducers | `src/core/state.py` | `candidates` and `candidate_scores` use `operator.add` |
| `Command` pattern | Gatekeeper nodes | Combines state update + routing in a single return |
| `Send` API | `whale_hunter.py` orchestrator | Parallel fan-out across 4 market regions |
| `InMemorySaver` | All 3 graphs | Checkpointing with `thread_id` for state persistence |
| `RetryPolicy` | All nodes | `max_attempts=3, initial_interval=2.0` for transient errors |
| `recursion_limit` | All `invoke()` calls | Set to 30 to prevent infinite loops |
| Structured output | Analyst nodes | `with_structured_output(InvestmentVerdict)` for validated verdicts |
| Subgraph | `src/agents/debate.py` | Pitcher/Skeptic/Judge multi-agent debate as nested graph |
| `@tool` decorator | `sec_edgar.py`, `finance_tools.py` | LangChain tool pattern for API integrations |
| `START` / `END` | All graphs | Modern entry-point API (no deprecated `set_entry_point`) |

---

## Key Modules

### The Interactive UI (`app.py`)
A Chainlit-powered chat interface.
- **`AUTO`** — Smart scan (yFinance screener + Brave trending)
- **`@Handle`** — Social scout from X/Twitter accounts
- **`PORTFOLIO`** — View the agent's paper trade track record
- **`BACKTEST`** — Run backtest on paper portfolio
- **Direct Ticker** — Type any ticker (e.g., `AAPL`) for an instant deep-dive

### The Morning Cron (`src/whale_hunter.py`)
Headless agent running as a **GitHub Action** daily cron. Hunts all 4 regions in parallel, evaluates candidates through the full pipeline, and emails HTML reports via Resend.

### Alpaca Paper Trading (`src/broker/alpaca.py`)
Optional **Alpaca Markets** integration for live paper trading of US equities:
- Automatically submits market orders for **BUY / STRONG BUY** verdicts on US tickers
- Calculates share quantity from Kelly position sizing and account equity
- Safety limits: minimum $1 order, maximum 25% of equity per position
- Records `order_id`, `fill_price`, and `broker_status` to VPS
- Dry-run mode when `ALPACA_ENABLED` is not set (logs but doesn't submit)

### VPS Data Layer (`vps/`)
Optional **FastAPI + DuckDB** backend deployed on a VPS (behind Tailscale) that replaces local JSON files for persistence:
- `seen_tickers` — Prevents re-analysing the same ticker (30 days for BUY/STRONG BUY, 14 days for AVOID/WATCH to allow re-evaluation)
- `paper_portfolio` — Records all paper trades with Kelly sizing, Alpaca order IDs, and fill prices
- `agent_runs` — Operational metrics for LangSmith correlation
- **Live Dashboard** (`GET /dashboard`) — Chart.js-powered portfolio dashboard with summary cards, verdict distribution donut, Kelly sizing bar chart, sortable trade table, and seen-ticker feed. Auto-refreshes every 5 minutes. Dark theme.
- `GET /portfolio/summary` — Lightweight aggregated stats endpoint (no yFinance calls)

The agent (`src/core/memory.py`, `src/portfolio_tracker.py`) auto-detects the VPS via `VPS_API_URL` env var and falls back to local JSON files when unavailable.

### Catalyst Polling Daemon (`vps/catalyst_poll.py`)
VPS-based systemd timer that polls every 15 minutes during US market hours for intraday triggers:
- **Volume spike** — Current volume > 3x average daily volume
- **Price move** — Intraday move > 10%
- **Insider filing** — New SEC Form 4 purchase for a tracked ticker

When triggered, fires a GitHub Actions `repository_dispatch` event to run the pipeline for that specific ticker.

### Grading Engine (`scripts/` + `src/core/online_eval.py`)
Automated quality assurance via LangSmith Evaluators, split into **offline** and **online** tiers:

**Offline Evaluators** (run on demand against golden dataset):
- `scripts/build_golden_dataset.py` — Curates 50 representative traces into a LangSmith Dataset
- `scripts/evaluators.py` — 5 custom evaluators:
  - **Catalyst Grounding** (LLM-as-a-Judge) — Scores whether claims are backed by data
  - **Company Identity** (LLM-as-a-Judge) — Catches "name-trap" hallucinations
  - **Format** — Validates headers, no duplicates, Kelly present for BUY
  - **Verdict Validity** — Ensures verdict is one of the 4 valid values
  - **Kelly Math** — Checks allocation is within [1%, 25%] bounds
- `scripts/run_evals.py` — Runs all evaluators against the golden dataset

**Online Evaluators** (run inline during every cron):
- `src/core/online_eval.py` — After each analyst verdict, the cheap evaluators (`format_score`, `verdict_validity_score`) run automatically and post results as **LangSmith feedback** on the run. Zero extra LLM cost.

**Annotation Queue**:
- WATCH, AVOID, and fallback-path verdicts are automatically tagged with `needs_review=true` in LangSmith metadata, so the team can filter and review edge cases in the LangSmith UI.

**Prompt A/B Testing**:
- `src/prompts/senior_broker.py` supports a `PROMPT_VERSION` env var to pin to a specific LangSmith Hub commit. Deploy two cron runs with different versions and compare results in LangSmith Experiments.

### SEC EDGAR Ground Truth (`src/sec_edgar.py`)
Fetches the most recent 10-K or 10-Q filing from SEC EDGAR for US equities and extracts two investment-critical sections:
- **Item 7: Management's Discussion & Analysis (MD&A)** — Management's own view of operations
- **Item 1A: Risk Factors** — Legally mandated disclosure of what could go wrong

Uses the EDGAR EFTS full-text search API with BeautifulSoup HTML parsing and `RecursiveCharacterTextSplitter` for section truncation. Injected into the analyst prompt as `{sec_context}` for non-US equities this is skipped gracefully.

### Structured Verdicts (`src/models/verdict.py`)
Pydantic model enforcing one of 4 verdict types:
```
STRONG BUY | BUY | WATCH | AVOID
```
Used via `llm.with_structured_output(InvestmentVerdict)` with a graceful fallback to plain text LLM output.

### Kelly Criterion Position Sizing (`src/models/kelly.py`)
Computes optimal position size from historical portfolio performance using the **Kelly Criterion**:
- Calculates win rate, average win %, and average loss % from VPS or local trade history
- Applies the Kelly formula: `f* = (win_rate / avg_loss) - ((1 - win_rate) / avg_win)`
- Uses conservative **half-Kelly** with verdict-based scaling:
  - `STRONG BUY` → 100% of half-Kelly
  - `BUY` → 70% of half-Kelly
  - `WATCH` → 30% of half-Kelly
- Clamped to **1% -- 25%** to prevent over-concentration
- Requires minimum 5 historical trades before activating (returns 0% otherwise)

Position sizing is computed **post-LLM** and injected into the `InvestmentVerdict` model, appearing in both the report output and the paper trade record.

---

## Quick Start Guide

### 1. Environment Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration (`.env`)
```env
OPENROUTER_API_KEY=your_key       # LLM Inference (6-model fallback chain)
FINNHUB_API_KEY=your_key          # Deep Fundamentals & Insider Data
BRAVE_API_KEY=your_key            # Web Search
RESEND_API_KEY_CISCO=your_key     # Email Reporting (Cron only)

# Optional: VPS Data API
VPS_API_URL=http://your-vps:8080
VPS_API_KEY=your_vps_key

# Optional: Alpaca Paper Trading (US equities only)
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_ENABLED=true

# Optional: Multi-Agent Debate
USE_DEBATE=true                   # Enable pitcher/skeptic/judge pipeline

# Optional: LangSmith Observability
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_key
LANGCHAIN_PROJECT=primogreedy

# Optional: Prompt A/B Testing
PROMPT_VERSION=latest             # Pin to a specific Hub commit hash for A/B tests
```

### 3. Launching the UI
```bash
chainlit run app.py -w
```

### 4. Running the Workflow CLI
```bash
python3 main.py
```

### 5. Deploying the VPS API (optional)
```bash
bash vps/deploy.sh
```

### 6. Running Evaluations (optional)
```bash
python scripts/build_golden_dataset.py   # Build the golden dataset from LangSmith
python scripts/run_evals.py              # Run all evaluators
```

---

## Project Structure

```
primogreedy/
├── app.py                          # Chainlit web UI entry point
├── main.py                         # Workflow CLI entry point
├── requirements.txt                # Python dependencies (LangChain 1.0 LTS)
├── src/
│   ├── agent.py                    # Interactive Chainlit pipeline (scout/gatekeeper/analyst)
│   ├── whale_hunter.py             # Daily cron pipeline + parallel Send orchestrator
│   ├── llm.py                      # OpenRouter LLM with 6-model fallback + structured output
│   ├── sec_edgar.py                # SEC EDGAR 10-K/10-Q filing fetcher + parser (@tool)
│   ├── finance_tools.py            # Finnhub tools (@tool decorated)
│   ├── portfolio_tracker.py        # Paper trade recording + Alpaca execution
│   ├── email_utils.py              # Resend email dispatch
│   ├── core/
│   │   ├── state.py                # AgentState (TypedDict with Annotated reducers + debate fields)
│   │   ├── memory.py               # Seen-tickers ledger (VPS or local JSON)
│   │   ├── search.py               # Brave Search wrapper (with retry/backoff)
│   │   ├── ticker_utils.py         # Ticker extraction, suffix resolution, noise filtering
│   │   ├── online_eval.py          # Inline LangSmith evaluators + annotation queue
│   │   └── logger.py               # Logging config
│   ├── models/
│   │   ├── verdict.py              # InvestmentVerdict Pydantic model (with header-stripping)
│   │   └── kelly.py                # Kelly Criterion position sizing (with 10-min cache)
│   ├── agents/
│   │   ├── debate.py               # Multi-agent pitcher/skeptic/judge subgraph
│   │   ├── data_collection_agent.py
│   │   ├── technical_analysis_agent.py
│   │   ├── news_intelligence_agent.py
│   │   └── portfolio_manager_agent.py
│   ├── broker/
│   │   └── alpaca.py               # Alpaca Paper Trading order router + execution
│   ├── workflows/
│   │   ├── workflow.py             # 4-node linear workflow graph
│   │   └── state.py                # Workflow-specific AgentState
│   ├── discovery/
│   │   ├── screener.py             # yFinance micro-cap screener
│   │   ├── scoring.py              # Quantitative candidate scoring
│   │   └── insider_feed.py         # SEC EDGAR / Finnhub insider data
│   └── prompts/
│       └── senior_broker.py        # LangSmith Hub prompt template
├── scripts/
│   ├── build_golden_dataset.py     # LangSmith golden dataset builder
│   ├── evaluators.py               # Custom LangSmith evaluators (5 scorers)
│   └── run_evals.py                # Evaluation runner
├── vps/
│   ├── api.py                      # FastAPI + DuckDB data API (with broker fields + dashboard)
│   ├── catalyst_poll.py            # Intraday catalyst polling daemon
│   ├── schema.sql                  # DuckDB table definitions
│   ├── deploy.sh                   # VPS deployment script
│   └── requirements.txt            # VPS-specific dependencies
└── .github/workflows/
    └── hunter.yml                  # Daily cron + catalyst dispatch GitHub Action
```

---

## The Philosophy

PrimoGreedy does not try to predict the future. It relies on strict **Benjamin Graham** math (`Intrinsic Value = sqrt(22.5 * EPS * BookValue)`) to establish a baseline Margin of Safety, then applies **Peter Lynch's** logic to find the catalyst and **Charlie Munger's** inversion to find the catch. It is designed to say **AVOID** far more often than it says **BUY**.
