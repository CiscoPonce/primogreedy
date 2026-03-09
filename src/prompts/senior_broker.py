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
"""


def get_analyst_prompt() -> str:
    """Return the Senior Broker prompt template string.

    Tries LangSmith Hub first (if LANGCHAIN_API_KEY is set), otherwise
    returns the local fallback.

    Supports ``PROMPT_VERSION`` env var for pinning to a specific Hub
    commit.  Set to "latest" (default) or a commit hash like
    "abc123def456" to lock a specific version during A/B testing.
    """
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
                return template_str
        except Exception as exc:
            logger.warning("Hub pull failed, using local fallback: %s", exc)

    logger.info("Using local Senior Broker prompt template")
    return SENIOR_BROKER_TEMPLATE
