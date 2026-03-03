# LangSmith Guide — PrimoGreedy

Your LangSmith project **`primogreedy`** is live at [smith.langchain.com](https://smith.langchain.com).  
Every LLM call from the **daily cron**, **HF Space**, and **local runs** is automatically traced.

---

## Features Available Now

### 1. Tracing (Runs tab)
Every LLM call is recorded with full context:
- **Inputs** — the exact prompt sent to the model
- **Outputs** — the raw LLM response
- **Latency** — how long each call took
- **Tokens** — input/output token counts
- **Errors** — 429 rate limits, 404s, timeouts
- **Tags** — `model:<name>`, `attempt:<n>` for filtering the fallback chain

**How to use:** Click `Tracing` → `primogreedy` → click any run to drill into details.

### 2. Monitoring (Dashboard)
Visual charts that aggregate data over time:
- Latency trends (P50/P99)
- Error rates
- Token usage
- Trace volume

Fills up automatically as the cron runs daily.

### 3. Prompts (Hub)
Your `senior-broker` prompt is versioned and editable in the browser.
- Edit the Munger/Lynch/Graham prompt without redeploying code
- Every edit creates a new commit (version history)
- The agent auto-pulls the latest Hub version at runtime

**How to use:** Click `Prompts` → `senior-broker` → edit directly.

### 4. Playground
Test prompts interactively against different models and inputs.

**How to use:**
1. Click `Prompts` → `senior-broker` → `Open in Playground`
2. Click the model selector → `Add custom model`
3. Configure OpenRouter:
   - **Base URL:** `https://openrouter.ai/api/v1`
   - **API Key:** your `OPENROUTER_API_KEY`
   - **Model:** e.g. `google/gemma-3-27b-it:free`
4. Fill in template variables (`ticker`, `price`, `eps`, etc.)
5. Click **Run** to test

### 5. Datasets & Experiments
Create test datasets to measure prompt quality over time.

**How to use:**
1. Click `Datasets & Experiments` → `+ New Dataset`
2. Add example inputs, e.g.:
   ```json
   {"ticker": "AAPL", "price": 264, "eps": 6.5, "book_value": 4.3, ...}
   ```
3. Run experiments against the dataset to compare prompt versions

### 6. Annotation Queues
Manually review and label agent runs for quality tracking.

**How to use:**
1. Click `Annotation Queues` → create a queue (e.g. "Verdict Quality")
2. From any trace, click the annotation icon → add labels like "accurate" / "hallucinated"
3. Over time this builds a quality dataset for measuring improvements

### 7. Search
Full-text search across all runs — find specific tickers, errors, or outputs.

**How to use:** Click `Search` in the sidebar → type a ticker, error message, or keyword.

### 8. Filtering by Tags
Find specific model failures or fallback events.

**How to use:**
1. Go to `Tracing` → `primogreedy`
2. Click `+ Add filter` → `Tags`
3. Examples:
   - `model:google/gemma-3-27b-it:free` — all calls to Gemma
   - `model:nvidia/nemotron-3-nano-30b-a3b:free` — primary model calls
   - Filter by `Error` column to find 429 rate limits

---

## Features That Need Extra Setup

### Studio (LangGraph Visualizer)
Step through the scout → gatekeeper → analyst pipeline visually in real-time.

**Setup steps:**
1. Install the CLI:
   ```bash
   pip install langgraph-cli
   ```
2. Create `langgraph.json` in the project root:
   ```json
   {
     "graphs": {
       "primogreedy": "./src/agent.py:app"
     },
     "env": ".env"
   }
   ```
3. Start the dev server:
   ```bash
   langgraph dev
   ```
4. Open Studio at `http://localhost:8123` in your browser
5. Send a ticker and watch each node execute step-by-step

### Automations (Alerts)
Get notified when errors spike or latency degrades.

**Setup steps:**
1. Click `Automations` in the sidebar
2. Click `+ New Rule`
3. Example rules:
   - **"429 Alert"** — trigger when error rate > 50% in the last hour
   - **"Latency Alert"** — trigger when P99 latency > 30 seconds
4. Choose action: webhook, Slack, or email

### Deployments (Hosted Agents)
Deploy your agent as a hosted API endpoint. **Not needed** for PrimoGreedy since you run via cron + HF Space.

---

## Most Useful Next Steps

### Step 1 — Set Up the Playground
This is the highest-value feature. Once configured, you can tweak the Senior Broker prompt and test it in seconds without touching code.

1. Go to `Prompts` → `senior-broker` → `Open in Playground`
2. Add OpenRouter as custom model provider (see instructions above)
3. Test a prompt with real ticker data
4. If the output is better, click **Commit** — the agent auto-pulls the new version

### Step 2 — Filter Your 429 Errors
After a few cron runs, check which models are rate-limiting:

1. `Tracing` → `primogreedy` → `+ Add filter` → `Tags` → `model:*`
2. Sort by `Error` column
3. Identify which models fail most → consider reordering `MODEL_CHAIN` in `llm.py`

### Step 3 — Annotate Verdicts for Quality Tracking
As the cron generates daily verdicts:

1. Click into a successful `analyst_node` trace
2. Read the verdict output
3. Add an annotation: "accurate" / "hallucinated" / "missed risk"
4. Over time, this gives you a concrete accuracy score to track

### Step 4 — Create a Benchmark Dataset
Build a "golden set" of tickers with known outcomes:

1. `Datasets` → `+ New Dataset` → name it "Benchmark Tickers"
2. Add 5-10 tickers with historical data where you know the right call
3. Run experiments whenever you change the prompt
4. Compare results across prompt versions

---

## Free Tier Limits

| Limit | Value |
|-------|-------|
| Trace retention | 14 days |
| Monthly traces | 5,000 |
| Workspaces | 1 |
| Team members | 1 |

Your daily cron generates ~5-15 traces per run, so you're well within limits (~150-450/month).
