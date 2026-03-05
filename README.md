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

**PrimoGreedy** is an automated, AI-driven financial analysis agent designed to hunt, filter, and evaluate Micro-Cap and Small-Cap stocks. It acts as a ruthless "Logic Firewall," aggressively rejecting high-debt, cash-burning, and overvalued companies before deploying a ReAct (Reasoning and Acting) LLM to write highly structured fundamental investment memos.

---

## Core Architecture (The LangGraph Engine)

The system is built on **LangGraph** following modern best practices (partial state updates, `Command` routing, `Send` parallel fan-out, checkpointing, and `RetryPolicy`).

### Hunter Pipeline (`src/agent.py` / `src/whale_hunter.py`)

```
START --> initial_routing --> [chat] --> END
                          \-> [scout] --> [gatekeeper] --Command--> [analyst] --> END
                                              \--Command--> [scout]  (retry)
```

1. **Scout Node** — Discovers candidates via yFinance screener + Brave Search trending, scores and ranks them, and pops the best unseen ticker.
2. **Gatekeeper Node** — Strict quantitative firewall using the `Command` pattern for routing:
   - Market Cap: $10M -- $300M
   - Share Price: under $30.00
   - Zombie Filter: rejects unprofitable companies with < 6 months cash runway
   - Routes directly to `analyst` (PASS / retries exhausted) or back to `scout` (FAIL) via `Command`.
3. **Analyst Node** — Senior Broker analysis powered by OpenRouter (5-model fallback chain). Calls Finnhub tools for deep fundamentals and insider sentiment. Fetches **SEC EDGAR** 10-K/10-Q filings for US equities (MD&A + Risk Factors ground truth). Returns structured `InvestmentVerdict` (Pydantic model) with guaranteed `STRONG BUY | BUY | WATCH | AVOID` verdicts plus **Kelly Criterion position sizing**, falling back to plain LLM output.

### Workflow Pipeline (`src/workflows/workflow.py`)

```
START --> [data_collection] --> [technical_analysis] --> [news_intelligence] --> [portfolio_manager] --> END
```

A linear 4-node pipeline for deep single-ticker analysis (used by `main.py` CLI).

### Parallel Region Orchestrator (`src/whale_hunter.py`)

The daily cron dispatches all 4 markets (USA, UK, Canada, Australia) **in parallel** via the LangGraph `Send` API:

```
START --> dispatch_regions --> [hunt_region: USA]       \
                          \-> [hunt_region: UK]         |-- region_results --> END
                          \-> [hunt_region: Canada]     |
                          \-> [hunt_region: Australia]  /
```

Each `hunt_region` invokes the full per-region subgraph (scout -> gatekeeper -> analyst -> email).

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

### VPS Data Layer (`vps/`)
Optional **FastAPI + DuckDB** backend deployed on a VPS (behind Tailscale) that replaces local JSON files for persistence:
- `seen_tickers` — Prevents re-analysing the same ticker within 30 days
- `paper_portfolio` — Records all BUY/STRONG BUY/WATCH paper trades with Kelly position sizing
- `agent_runs` — Operational metrics for LangSmith correlation

The agent (`src/core/memory.py`, `src/portfolio_tracker.py`) auto-detects the VPS via `VPS_API_URL` env var and falls back to local JSON files when unavailable.

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
OPENROUTER_API_KEY=your_key       # LLM Inference (5-model fallback chain)
FINNHUB_API_KEY=your_key          # Deep Fundamentals & Insider Data
BRAVE_API_KEY=your_key            # Web Search
RESEND_API_KEY_CISCO=your_key     # Email Reporting (Cron only)

# Optional: VPS Data API
VPS_API_URL=http://your-vps:8080
VPS_API_KEY=your_vps_key
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
│   ├── llm.py                      # OpenRouter LLM with 5-model fallback chain
│   ├── sec_edgar.py                # SEC EDGAR 10-K/10-Q filing fetcher + parser (@tool)
│   ├── finance_tools.py            # Finnhub tools (@tool decorated)
│   ├── portfolio_tracker.py        # Paper trade recording + evaluation (with position_size)
│   ├── email_utils.py              # Resend email dispatch
│   ├── core/
│   │   ├── state.py                # AgentState (TypedDict with Annotated reducers)
│   │   ├── memory.py               # Seen-tickers ledger (VPS or local JSON)
│   │   ├── search.py               # Brave Search wrapper
│   │   ├── ticker_utils.py         # Ticker extraction, suffix resolution
│   │   └── logger.py               # Logging config
│   ├── models/
│   │   ├── verdict.py              # InvestmentVerdict Pydantic model (with Kelly fields)
│   │   └── kelly.py                # Kelly Criterion position sizing calculator
│   ├── agents/                     # Workflow pipeline nodes
│   │   ├── data_collection_agent.py
│   │   ├── technical_analysis_agent.py
│   │   ├── news_intelligence_agent.py
│   │   └── portfolio_manager_agent.py
│   ├── workflows/
│   │   ├── workflow.py             # 4-node linear workflow graph
│   │   └── state.py                # Workflow-specific AgentState
│   ├── discovery/
│   │   ├── screener.py             # yFinance micro-cap screener
│   │   ├── scoring.py              # Quantitative candidate scoring
│   │   └── insider_feed.py         # SEC EDGAR / Finnhub insider data
│   └── prompts/
│       └── senior_broker.py        # LangSmith Hub prompt template
├── vps/
│   ├── api.py                      # FastAPI + DuckDB data API
│   ├── schema.sql                  # DuckDB table definitions
│   ├── deploy.sh                   # VPS deployment script
│   └── requirements.txt            # VPS-specific dependencies
└── .github/workflows/
    └── hunter.yml                  # Daily cron GitHub Action
```

---

## The Philosophy

PrimoGreedy does not try to predict the future. It relies on strict **Benjamin Graham** math (`Intrinsic Value = sqrt(22.5 * EPS * BookValue)`) to establish a baseline Margin of Safety, then applies **Peter Lynch's** logic to find the catalyst and **Charlie Munger's** inversion to find the catch. It is designed to say **AVOID** far more often than it says **BUY**.
