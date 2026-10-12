"""Senior Broker prompt template — the core analytical prompt for PrimoGreedy.

This module provides the analyst prompt via two paths:
  1. **LangSmith Hub** — pulled at runtime so the team can edit, version, and
     A/B test prompt changes *without* redeploying code.
  2. **Local fallback** — hard-coded below so the agent still works offline
     or if Hub is unreachable.

To upload / update the Hub prompt, run:
    PYTHONPATH=. python scripts/push_prompt_to_hub.py
"""

import os
from src.core.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Local template — kept in sync with the Hub version
# ---------------------------------------------------------------------------
SENIOR_BROKER_TEMPLATE = """Act as a Senior Financial Broker evaluating {company_name} ({ticker}).

HARD DATA: Price: ${price} | EPS: {eps} | Book/Share: {book_value} | EBITDA: {ebitda}
QUANTITATIVE THESIS: {thesis}

{deep_fundamentals}

{sec_context}

Your task is to write a highly structured investment memo combining strict {strategy} math with qualitative analysis and recent insider behavior/news. Do not use fluff or buzzwords.

Format your response EXACTLY like this:

### THE QUANTITATIVE BASE (Graham / Asset Play)
* State the current Price vs the calculated {strategy} valuation.
* Briefly explain if the math supports a margin of safety.

### THE LYNCH PITCH (Why I would own this)
* **The Core Action:** In one sentence, what are insiders doing (buying/selling/neutral)?
* **The Catalyst:** Based on the news, what is the ONE simple reason this stock could run?

### THE MUNGER INVERT (How I could lose money)
* **Structural Weakness:** What is the most likely way an investor loses money here based on fundamentals/news?
* **The Bear Evidence:** What exact metric, news, or math would prove the bear case right?

### FINAL VERDICT
STRONG BUY / BUY / WATCH / AVOID (Choose one, followed by a 1-sentence bottom line).

### FRAMEWORK OUTPUT (structured fields — 3-layer method)
Fill every structured field you emit for which the data supports an answer; otherwise leave it blank and say so under data_quality. No fluff.

- business_summary: one paragraph on the business model and its sector.
- financial_evolution: 3-4 lines of revenue / EPS / EBITDA trend over recent periods.
- cash_flow: operating cash flow vs net income, FCF sign and trajectory.
- debt_liquidity: total debt, debt/equity, current ratio, refinancing risk.
- dilution: share-count trend, shelf/ATM risk, recent raises.
- valuation_method: the method(s) used (multiples, DCF, asset-based, sum-of-parts). NEVER rely on a single formula in isolation; triangulate.
- valuation_bear / valuation_base / valuation_bull: a number for each scenario WITH the one assumption driving it.
- valuation_range_low / valuation_range_high: the numeric fair-value range (numbers only).
- margin_of_safety: current price vs the base scenario as a %.
- risks: the top 3-5 SPECIFIC risks, each with the reason it matters for this name.
- invalidation_facts: the exact fact(s) (metric, filing, event) whose publication would break the thesis.
- macro_factors: the rate / inflation / sector-cycle links that actually affect the outcome.
- portfolio_fit: how this fits a diversified portfolio — position cap, sector overlap, liquidity.
- sources: list each quantitative claim used, as [{type,url,date,period}] (filings/price/analyst/news). SEPARATE published facts from your estimates and from your inferences. Mark anything not verified as "no verificado".
- data_quality: which inputs are published facts, which are estimates, which are your inferences, and which data is PENDING and would change the conclusion.
- proposed_action: one of INITIATE / MAINTAIN / INCREASE / REDUCE / CLOSE / DISCARD / PENDING.
- review_trigger: the concrete market or company event that should trigger the next review.
- next_review_at: a concrete date (YYYY-MM-DD).

Rules (never break these):
- No universal PER / debt / valuation thresholds as the conclusion.
- A price alert is NOT an order — it only starts a review; never propose an automatic buy/sell.
- If valuation, risk or portfolio-fit data is missing, say so and mark the conclusion provisional.
"""


_cached_prompt: str | None = None


def get_analyst_prompt() -> str:
    """Return the Senior Broker prompt template string.

    Tries LangSmith Hub first (if LANGCHAIN_API_KEY is set), otherwise
    returns the local fallback.  Result is cached after first call.

    Supports ``PROMPT_VERSION`` env var for pinning to a specific Hub
    commit.  Set to "latest" (default) or a commit hash like
    "abc123def456" to lock a specific version during A/B testing.
    """
    global _cached_prompt
    if _cached_prompt is not None:
        return _cached_prompt

    if os.getenv("LANGCHAIN_API_KEY"):
        try:
            from langsmith import Client

            client = Client()
            version = os.getenv("PROMPT_VERSION", "latest").strip()
            prompt_id = "primogreedy/senior-broker"
            if version and version != "latest":
                prompt_id = f"{prompt_id}:{version}"
                logger.info("Pulling Hub prompt pinned to %s", version[:12])

            hub_prompt = client.pull_prompt(prompt_id)
            logger.info("Loaded analyst prompt from Hub (%s)", version)

            messages = hub_prompt.messages
            if messages:
                template_str = messages[0].prompt.template
                _cached_prompt = template_str
                return _cached_prompt
        except Exception as exc:
            logger.debug("Hub prompt unavailable, using local template: %s", exc)

    logger.info("Using local Senior Broker prompt template")
    _cached_prompt = SENIOR_BROKER_TEMPLATE
    return _cached_prompt
