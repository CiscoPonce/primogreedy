# 🕵️‍♂️ PrimoGreedy Agent

**PrimoGreedy** is an automated, AI-driven financial analysis agent designed to hunt, filter, and evaluate Micro-Cap and Small-Cap stocks. It acts as a ruthless "Logic Firewall," aggressively rejecting high-debt, cash-burning, and overvalued companies before deploying a ReAct (Reasoning and Acting) LLM to write highly structured fundamental investment memos.

---

## 🏗️ Core Architecture (The LangGraph Engine)

The system is built on **LangGraph**, routing potential stock candidates through a sequential, state-managed pipeline:

1. **Scout Node (`scout_node`):**
   - Autonomously scours the web (via Brave Search, X/Twitter accounts, or direct user input) to find an interesting stock ticker.
2. **Gatekeeper Node (`gatekeeper_node`):**
   - The strict **Quantitative Firewall**. It instantly rejects companies failing baseline algorithmic tests:
     - **Market Cap:** Must be between $10M and $300M.
     - **Share Price:** Must be under $30.00.
     - **The Zombie Filter:** Rejects any unprofitable company with less than 6 months of cash runway remaining.
3. **Analyst Node (`analyst_node`):**
   - A **ReAct Agent** (powered by OpenRouter LLMs like Solar Pro or Gemini). 
   - Before writing its memo, it dynamically calls **Finnhub Tools** (`finance_tools.py`) to gather deep fundamentals (Beta, ROE), check insider buying/selling sentiment, and pull direct financial news.
   - Outputs a highly structured memo answering three frameworks: **The Quantitative Base** (Graham), **The Lynch Pitch**, and **The Munger Invert**.

---

## 🚀 Key Modules

### 1. The Interactive UI (`app.py`)
A Chainlit-powered chat interface where you can interact directly with the agent. 
- **Command `AUTO`**: The agent scans the global market for trending tickers and evaluates them.
- **Command `@Handle`**: The agent targets specific financial X (Twitter) accounts to extract their latest stock picks.
- **Direct Ticker**: Type any ticker (e.g., `AAPL`) for an instant fundamental deep-dive.

### 2. The Morning Cron (`src/whale_hunter.py`)
A headless version of the agent designed to run as a **GitHub Action** every morning. It autonomously hunts a new stock, runs it through the Gatekeeper, has the Analyst ReAct node evaluate it, and sends a beautifully formatted HTML report directly to your inbox via Resend.

---

## ⚙️ Quick Start Guide

### 1. Environment Setup
Create a virtual environment and install the required LangChain and Finnhub dependencies:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration (`.env`)
You must provide the following API keys in your `.env` file for the agent to function:
```env
OPENROUTER_API_KEY=your_key_here    # LLM Inference
FINNHUB_API_KEY=your_key_here       # Deep Fundamentals & Insider Data
BRAVE_API_KEY=your_key_here         # Web Search
RESEND_API_KEY=your_key_here        # Email Reporting (Cron only)
```

### 3. Launching the UI
To start the interactive chat interface locally:
```bash
chainlit run app.py -w
```

## 🧠 The Philosophy
PrimoGreedy does not try to predict the future. It relies on strict Benjamin Graham math (Intrinsic Value = Sqrt(22.5 * EPS * BookValue)) to establish a baseline Margin of Safety, and then relies on Peter Lynch's logic and Charlie Munger's inversion to find the catch. It is designed to say "AVOID" far more often than it says "BUY."
