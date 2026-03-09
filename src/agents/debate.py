"""Multi-agent investment debate: Pitcher -> Skeptic -> Judge.

The debate replaces the single-LLM analyst call with three specialised
agents that produce a more rigorous, hallucination-resistant verdict.

Toggle: set ``USE_DEBATE=true`` in the environment to enable.

Architecture (LangGraph subgraph):
    pitcher_node (Trinity) -> bull_case
    skeptic_node (GLM)     -> bear_case
    judge_node   (StepFun) -> InvestmentVerdict
"""

import os
import warnings
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy

from src.core.logger import get_logger
from src.llm import MODEL_CHAIN

logger = get_logger(__name__)

PITCHER_MODEL = os.getenv("DEBATE_PITCHER_MODEL", "arcee-ai/trinity-large-preview:free")
SKEPTIC_MODEL = os.getenv("DEBATE_SKEPTIC_MODEL", "z-ai/glm-4.5-air:free")
JUDGE_MODEL = os.getenv("DEBATE_JUDGE_MODEL", MODEL_CHAIN[0])

# Free-tier rate limits: 8 req/min per model.
# Add delays between debate LLM calls to avoid 429s.
_DEBATE_DELAY = int(os.getenv("DEBATE_DELAY_SECONDS", "10"))


class DebateState(TypedDict, total=False):
    """Internal state for the debate subgraph."""
    ticker: str
    company_name: str
    financial_data_summary: str
    deep_fundamentals: str
    sec_context: str
    strategy: str
    price: float
    eps: float
    book_value: float
    ebitda: float
    bull_case: str
    bear_case: str
    final_verdict: str


def _make_llm(model: str, max_tokens: int = 2048):
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
        temperature=0,
        max_tokens=max_tokens,
        request_timeout=90,
    )


def _resilient_llm_call(model: str, prompt: str, fallback_model: str) -> str:
    import time
    time.sleep(_DEBATE_DELAY)  # Initial spacing

    llm = _make_llm(model)
    # Try primary model with backoff
    for attempt in range(4):
        try:
            return llm.invoke(prompt).content
        except Exception as exc:
            if "429" in str(exc):
                sleep_time = 15 * (attempt + 1)
                logger.warning("Rate limit on %s (attempt %d). Sleeping %ds...", model, attempt + 1, sleep_time)
                time.sleep(sleep_time)
            elif "404" in str(exc) or "too short" in str(exc):
                break  # Fatal error, move to fallback
            else:
                logger.warning("Error on %s: %s", model, exc)
                time.sleep(5)

    # Fallback model
    logger.warning("Primary model %s exhausted, switching to fallback %s", model, fallback_model)
    fallback = _make_llm(fallback_model)
    for attempt in range(3):
        try:
            return fallback.invoke(prompt).content
        except Exception as exc:
            if "429" in str(exc):
                time.sleep(20)
            else:
                time.sleep(5)
                
    raise RuntimeError(f"Both {model} and {fallback_model} failed due to rate limits.")


# ---------------------------------------------------------------------------
# Node 1 — The Pitcher (bullish thesis)
# ---------------------------------------------------------------------------

def pitcher_node(state: DebateState) -> dict:
    """Build the strongest possible investment thesis for the ticker."""
    ticker = state.get("ticker", "")
    company = state.get("company_name", ticker)
    fundamentals = state.get("deep_fundamentals", "")
    sec = state.get("sec_context", "")
    price = state.get("price", 0)
    eps = state.get("eps", 0)
    bv = state.get("book_value", 0)
    ebitda = state.get("ebitda", 0)

    prompt = (
        f"You are a bullish stock pitcher. Write the strongest possible "
        f"investment thesis for {company} ({ticker}).\n\n"
        f"HARD DATA: Price=${price} | EPS={eps} | Book/Share={bv} | EBITDA={ebitda}\n\n"
    )
    if fundamentals:
        prompt += f"FUNDAMENTALS:\n{fundamentals[:3000]}\n\n"
    if sec:
        prompt += f"SEC FILINGS:\n{sec[:2000]}\n\n"

    prompt += (
        "Focus on:\n"
        "1. Insider activity (buying signals)\n"
        "2. The ONE catalyst that could drive the stock higher\n"
        "3. Valuation upside (margin of safety math)\n"
        "4. Competitive advantages or turnaround signals\n\n"
        "Be specific and data-driven. Only cite facts present in the data above."
    )

    bull_case = _resilient_llm_call(PITCHER_MODEL, prompt, MODEL_CHAIN[0])

    logger.info("Pitcher delivered bull case for %s (%d chars)", ticker, len(bull_case))
    return {"bull_case": bull_case}


# ---------------------------------------------------------------------------
# Node 2 — The Skeptic (bearish challenge)
# ---------------------------------------------------------------------------

def skeptic_node(state: DebateState) -> dict:
    """Challenge the bull case with skeptical analysis grounded in data."""
    ticker = state.get("ticker", "")
    company = state.get("company_name", ticker)
    bull_case = state.get("bull_case", "")
    fundamentals = state.get("deep_fundamentals", "")
    sec = state.get("sec_context", "")
    price = state.get("price", 0)
    eps = state.get("eps", 0)
    bv = state.get("book_value", 0)
    ebitda = state.get("ebitda", 0)

    prompt = (
        f"You are a skeptical risk analyst. Read the BULL CASE below and "
        f"tear it apart for {company} ({ticker}).\n\n"
        f"HARD DATA: Price=${price} | EPS={eps} | Book/Share={bv} | EBITDA={ebitda}\n\n"
    )
    if fundamentals:
        prompt += f"FUNDAMENTALS:\n{fundamentals[:3000]}\n\n"
    if sec:
        prompt += f"SEC FILINGS:\n{sec[:2000]}\n\n"

    prompt += (
        f"BULL CASE TO CHALLENGE:\n{bull_case[:3000]}\n\n"
        "Your job:\n"
        "1. Use ONLY the provided data — if a claim has no evidence in the data, "
        "call it out as FABRICATED\n"
        "2. Identify the biggest risk to an investor\n"
        "3. Point out any math errors or unsupported assumptions\n"
        "4. State what specific evidence would prove the bear case right\n\n"
        "Be thorough but concise."
    )

    bear_case = _resilient_llm_call(SKEPTIC_MODEL, prompt, MODEL_CHAIN[0])

    logger.info("Skeptic delivered bear case for %s (%d chars)", ticker, len(bear_case))
    return {"bear_case": bear_case}


# ---------------------------------------------------------------------------
# Node 3 — The Judge (final verdict with structured output)
# ---------------------------------------------------------------------------

def judge_node(state: DebateState) -> dict:
    """Synthesise the debate into a structured InvestmentVerdict."""
    from src.models.verdict import InvestmentVerdict
    from src.llm import get_structured_llm

    ticker = state.get("ticker", "")
    company = state.get("company_name", ticker)
    bull_case = state.get("bull_case", "")
    bear_case = state.get("bear_case", "")
    sec = state.get("sec_context", "")
    price = state.get("price", 0)
    eps = state.get("eps", 0)
    bv = state.get("book_value", 0)
    ebitda = state.get("ebitda", 0)
    strategy = state.get("strategy", "GRAHAM CLASSIC")

    prompt = (
        f"You are the Chief Investment Officer making the final call on "
        f"{company} ({ticker}).\n\n"
        f"HARD DATA: Price=${price} | EPS={eps} | Book/Share={bv} | EBITDA={ebitda}\n\n"
        f"BULL CASE (from the Pitcher):\n{bull_case[:3000]}\n\n"
        f"BEAR CASE (from the Skeptic):\n{bear_case[:3000]}\n\n"
    )
    if sec:
        prompt += f"SEC FILINGS:\n{sec[:2000]}\n\n"

    prompt += (
        "RULES:\n"
        "1. If the Skeptic flagged any claims as FABRICATED, you MUST downgrade\n"
        "2. Weight data-backed arguments more heavily\n"
        "3. Use strict " + strategy + " math for the quantitative base\n"
        "4. Your verdict must be one of: STRONG BUY, BUY, WATCH, AVOID\n\n"
        "Produce a structured investment memo with:\n"
        "- quantitative_base: Price vs valuation math\n"
        "- lynch_pitch: The best data-backed catalyst\n"
        "- munger_invert: The key risk from the bear case\n"
        "- verdict: Your final call\n"
        "- bottom_line: One sentence summary"
    )

    structured_llm = get_structured_llm(max_tokens=4096).with_structured_output(
        InvestmentVerdict
    )

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Pydantic serializer warnings")
        result = structured_llm.invoke(prompt)

    verdict_text = result.to_report()
    logger.info("Judge delivered verdict for %s: %s", ticker, result.verdict)

    return {"final_verdict": verdict_text, "_structured_result": result}


# ---------------------------------------------------------------------------
# Compile the debate subgraph
# ---------------------------------------------------------------------------

_debate_retry = RetryPolicy(max_attempts=4, initial_interval=15.0, backoff_factor=1.5)

_debate_graph = StateGraph(DebateState)
_debate_graph.add_node("pitcher", pitcher_node, retry=_debate_retry)
_debate_graph.add_node("skeptic", skeptic_node, retry=_debate_retry)
_debate_graph.add_node("judge", judge_node, retry=_debate_retry)
_debate_graph.add_edge(START, "pitcher")
_debate_graph.add_edge("pitcher", "skeptic")
_debate_graph.add_edge("skeptic", "judge")
_debate_graph.add_edge("judge", END)

debate_app = _debate_graph.compile()


def run_debate(
    ticker: str,
    company_name: str,
    financial_data_summary: str,
    deep_fundamentals: str,
    sec_context: str,
    strategy: str,
    price: float,
    eps: float,
    book_value: float,
    ebitda: float,
) -> dict:
    """Run the full pitcher -> skeptic -> judge debate for a ticker.

    Returns a dict with ``final_verdict`` (str), ``bull_case``, ``bear_case``,
    and ``_structured_result`` (InvestmentVerdict).
    """
    initial_state: DebateState = {
        "ticker": ticker,
        "company_name": company_name,
        "financial_data_summary": financial_data_summary,
        "deep_fundamentals": deep_fundamentals,
        "sec_context": sec_context,
        "strategy": strategy,
        "price": price,
        "eps": eps,
        "book_value": book_value,
        "ebitda": ebitda,
    }

    result = debate_app.invoke(initial_state)
    return {
        "final_verdict": result.get("final_verdict", ""),
        "bull_case": result.get("bull_case", ""),
        "bear_case": result.get("bear_case", ""),
        "_structured_result": result.get("_structured_result"),
    }


def is_debate_enabled() -> bool:
    """Check if multi-agent debate mode is turned on."""
    return os.getenv("USE_DEBATE", "").lower() in ("true", "1", "yes")
