# PrimoGreedy v7.0 — Team Guide

## What Is PrimoGreedy?

PrimoGreedy is an AI-powered micro-cap stock discovery agent. It systematically hunts for undervalued companies with market caps between **$10M and $300M**, priced under **$30/share**, across four markets: **USA, UK, Canada, and Australia**. It uses Benjamin Graham's value investing math combined with real-time data from multiple APIs, then delivers structured investment memos to the team via email.

The system runs **automatically every day at 6:00 AM UTC** via a GitHub Actions cron job, and also has an **interactive web UI** (Chainlit) for on-demand analysis.

---

## How It Works — The Pipeline

Every stock goes through a 4-step pipeline built with [LangGraph](https://langchain-ai.github.io/langgraph/):

```
SCOUT  ──>  GATEKEEPER  ──>  ANALYST  ──>  EMAIL
```

### Step 1: Scout (Discovery)

The Scout uses a **two-pronged approach** to find candidates:

1. **yFinance Screener** — Directly queries Yahoo Finance for stocks matching our market cap and price criteria. Uses a seed pool of known micro-cap tickers per region, updated over time.
2. **Brave Search Trending** — Searches the web for currently-trending micro-cap discussions (Reddit, financial blogs, news). Extracts ticker symbols from the results and merges them into the screener pool.

Both feeds are merged, then run through a **quantitative scoring system** (0–100 points) that ranks candidates on:

| Criterion | Points |
|-----------|--------|
| Profitability (EPS > 0) | 20 |
| Graham Number undervaluation | 25 |
| Price-to-Book < 1.0 | 15 |
| Free Cash Flow positive | 15 |
| Low Debt (Net Debt/EBITDA) | 10 |
| Current Ratio > 1.5 | 10 |
| Cash runway (if unprofitable) | 5 |

Only the **highest-scoring candidate** gets sent to the expensive LLM analyst step. Up to 4 backups are queued in case the top pick fails the Gatekeeper.

### Step 2: Gatekeeper (Validation)

Hard filters that reject stocks automatically:

- **Price** > $30/share
- **Market cap** outside $10M–$300M
- **Financial health** fails sector-specific checks:
  - *Banks*: Price/Book must be near or under 1.0
  - *Tech/Healthcare*: Must have at least 6 months of cash runway (zombie filter)
  - *Industrials/Default*: Net Debt/EBITDA must be under 3.5x

If rejected, the pipeline loops back to Scout to try the next candidate. After 4 failed attempts per region, a failure report is sent instead.

### Step 3: Analyst (AI Investment Memo)

For stocks that pass the Gatekeeper, the AI writes a structured investment memo using two valuation frameworks:

- **Graham Classic** — For profitable companies: `sqrt(22.5 × EPS × BookValue)` to calculate intrinsic value and margin of safety.
- **Deep Value Asset Play** — For unprofitable companies (miners, biotech, turnarounds): evaluates Price vs Book Value as the safety net.

The memo is structured in 4 sections:

1. **Quantitative Base** — Price vs calculated intrinsic value, margin of safety math
2. **Lynch Pitch** — What insiders are doing + the one catalyst that could move the stock
3. **Munger Invert** — How you could lose money, what metric proves the bear case
4. **Final Verdict** — STRONG BUY / BUY / WATCH / AVOID with a one-sentence bottom line

**Data sources fed into the prompt:**
- yFinance (price, EPS, book value, EBITDA, sector)
- Finnhub (insider sentiment, company news, 52W high/low, beta, ROE)
- Insider Feed (6-month insider buying/selling via MSPR score)
- Brave Search (recent news and catalysts)

### Step 4: Email

The memo (or failure report) is sent to all team members via Resend. Each team member uses their own API key for reliability.

---

## How to Use the Web UI (Chainlit)

The Chainlit app runs on Hugging Face Spaces. Commands:

| Command | What It Does |
|---------|-------------|
| `AUTO` | Runs the full screener + Brave scan and returns a hot list |
| `NVDA` | Analyses a single ticker through the full pipeline |
| `NVDA, AMD, TSLA` | Analyses multiple tickers |
| `@DeItaone` | Scouts the web for stocks mentioned by a social media personality |
| `PORTFOLIO` | Shows the paper portfolio with live P&L for all past calls |
| `BACKTEST` | Runs a Backtrader backtest on the paper portfolio vs Buy & Hold |
| `What is the Graham Number?` | Chat mode — ask the AI broker anything |

---

## Architecture Overview

```
src/
├── core/              # Shared modules used by everything
│   ├── logger.py      # Structured logging
│   ├── search.py      # Single Brave Search implementation
│   ├── ticker_utils.py # Ticker extraction, suffix resolution, price normalization
│   ├── memory.py      # Seen-tickers ledger (30-day TTL)
│   └── state.py       # Shared LangGraph state schema
│
├── discovery/          # Stock discovery and ranking
│   ├── screener.py    # yFinance micro-cap screener + Brave trending merge
│   ├── scoring.py     # Quantitative 0-100 scoring system
│   └── insider_feed.py # SEC Form 4 + Finnhub insider sentiment feeds
│
├── backtesting/        # Backtrader integration
│   ├── engine.py      # Cerebro setup and run helpers
│   ├── strategies.py  # PrimoAgent and Buy & Hold strategies
│   ├── portfolio_bridge.py # Converts paper portfolio into backtest signals
│   ├── data.py        # Data loading for backtests
│   ├── plotting.py    # Chart generation
│   └── reporting.py   # Markdown report generation
│
├── whale_hunter.py    # Daily cron pipeline (Scout→Gatekeeper→Analyst→Email)
├── agent.py           # Interactive Chainlit pipeline (adds Chat, Chart, manual lookup)
├── llm.py             # LLM connection with 6-model fallback chain
├── finance_tools.py   # Graham Number, health checks, Finnhub tools
├── portfolio_tracker.py # Paper portfolio ledger
├── email_utils.py     # Thread-safe email dispatch via Resend
├── scanner.py         # Trending stock scanner
├── social_scout.py    # Social media handle scouting
├── niche_hunter.py    # Standalone Brave-only global hunter
└── global_router.py   # Market config (suffixes, gov filing links)

app.py                 # Chainlit web UI entry point
backtest.py            # Interactive backtesting CLI
main.py                # Workflow-based analysis CLI
```

---

## API Keys Required

| Key | Service | Used For |
|-----|---------|----------|
| `OPENROUTER_API_KEY` | OpenRouter | LLM access (free tier) |
| `BRAVE_API_KEY` | Brave Search | Web search for trending stocks and news |
| `FINNHUB_API_KEY` | Finnhub | Insider sentiment, company news, fundamentals |
| `RESEND_API_KEY_*` | Resend | Email delivery (one per team member) |
| `EMAIL_*` | — | Team member email addresses |

All keys are stored as GitHub Secrets for the cron job and in `.env` for local development.

---

## LLM Model Fallback

The system uses free models via OpenRouter. If one model is rate-limited (429 error), it automatically tries the next:

1. `nvidia/nemotron-3-nano-30b-a3b` (primary — fast, reliable)
2. `stepfun/step-3.5-flash`
3. `arcee-ai/trinity-large-preview`
4. `google/gemma-3-27b-it`
5. `meta-llama/llama-3.3-70b-instruct`
6. `mistralai/mistral-small-3.1-24b-instruct`

---

## Daily Cron Schedule

The GitHub Actions workflow (`.github/workflows/hunter.yml`) runs at **06:00 UTC daily**:

1. Hunts all 4 regions: USA, UK, Canada, Australia
2. Sends email reports for each region (success or failure)
3. Commits the updated `seen_tickers.json` memory ledger to the repo
4. 60-minute timeout

To trigger manually: go to **Actions > Global Hunter Cron > Run workflow** on GitHub.

---

## Paper Portfolio

Every BUY / STRONG BUY / WATCH call is recorded in `paper_portfolio.json` with the entry price and date. Use `PORTFOLIO` in the UI to see live P&L, or `BACKTEST` to compare against a Buy & Hold baseline using Backtrader.

---

## Key Concepts

- **Graham Number**: `sqrt(22.5 × EPS × Book Value)` — the maximum price a defensive investor should pay. If the current price is below this, there's a margin of safety.
- **Margin of Safety**: `(Graham Value - Price) / Graham Value` — the bigger this %, the more undervalued.
- **MSPR (Monthly Share Purchase Ratio)**: Finnhub's insider sentiment metric. Positive = insiders buying, negative = insiders selling.
- **Zombie Filter**: Rejects tech/healthcare companies burning cash with less than 6 months of runway.
- **Seen Tickers Memory**: A JSON ledger that prevents re-analysing the same stock within 30 days.
