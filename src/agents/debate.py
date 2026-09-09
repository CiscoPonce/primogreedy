"""Multi-agent investment debate: Pitcher -> Skeptic -> Judge.

The debate replaces the single-LLM analyst call with three specialised
agents that produce a more rigorous, hallucination-resistant verdict.

Toggle: set ``USE_DEBATE=true`` in the environment to enable.

Architecture (LangGraph subgraph) — three DISTINCT role models:
    pitcher_node (Nemotron 3.5)       -> bull_case
    skeptic_node (Nemotron Super)     -> bear_case
    judge_node   (Dots3 Note)         -> InvestmentVerdict

The judge is resilient: it retries each structured-output model on empty
responses / transient errors, walks a fallback chain of structured-capable
models, and only as a last resort parses a plain-LLM JSON response.  If every
path fails it raises so the caller can fall back to the single-LLM analyst.
"""

import os
import json
import re
import time
import warnings
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy

from src.core.logger import get_logger
from src.llm import MODEL_CHAIN
from src.models.verdict import InvestmentVerdict

logger = get_logger(__name__)

PITCHER_MODEL = os.getenv("DEBATE_PITCHER_MODEL", "nvidia/nemotron-3.5-lightning:free")
SKEPTIC_MODEL = os.getenv("DEBATE_SKEPTIC_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
# Judge is the structured-output role; default to the dedicated non-reasoning
# JSON model so it stays DISTINCT from pitcher and skeptic by default.
JUDGE_MODEL = os.getenv("DEBATE_JUDGE_MODEL", "dots-studio/dots-3-note-preview:free")

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
    currency: str
    market_cap: float
    revenue: float
    revenue_growth: float
    total_cash: float
    total_debt: float
    current_ratio: float
    cash_per_share: float
    enterprise_value: float
    bull_case: str
    bear_case: str
    final_verdict: str
    _structured_result: InvestmentVerdict


_CURRENCY_SYMBOLS = {"USD": "$", "GBP": "£", "EUR": "€", "CAD": "C$", "AUD": "A$"}


def _fmt(value: float, currency: str) -> str:
    sym = _CURRENCY_SYMBOLS.get((currency or "USD").upper(), (currency or "USD") + " ")
    try:
        return f"{sym}{value:,.2f}"
    except (TypeError, ValueError):
        return f"{sym}{value}"


def _hard_data_line(state: DebateState) -> str:
    """Build a single enriched HARD-DATA line shared by all debate nodes."""
    currency = state.get("currency", "USD")
    price = state.get("price", 0)
    eps = state.get("eps", 0)
    bv = state.get("book_value", 0)
    ebitda = state.get("ebitda", 0)

    parts = [
        f"Price={_fmt(price, currency)}",
        f"EPS={eps:.4f}",
        f"Book/Share={bv:.4f}",
        f"EBITDA={_fmt(ebitda, currency)}",
    ]
    if state.get("market_cap"):
        parts.append(f"MCap={_fmt(state['market_cap'], currency)}")
    if state.get("revenue"):
        parts.append(f"Revenue={_fmt(state['revenue'], currency)}")
    if state.get("revenue_growth") is not None:
        parts.append(f"Rev Growth={state['revenue_growth']}")
    if state.get("total_cash"):
        parts.append(f"Cash={_fmt(state['total_cash'], currency)}")
    if state.get("total_debt"):
        parts.append(f"Debt={_fmt(state['total_debt'], currency)}")
    if state.get("cash_per_share"):
        parts.append(f"Cash/Share={_fmt(state['cash_per_share'], currency)}")
    if state.get("current_ratio"):
        parts.append(f"Current Ratio={state['current_ratio']}")
    if state.get("enterprise_value") and state.get("revenue"):
        evr = state["enterprise_value"] / state["revenue"]
        parts.append(f"EV/Rev={evr:.2f}x")
    return " | ".join(parts)


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
    time.sleep(_DEBATE_DELAY)  # Initial spacing

    llm = _make_llm(model)
    # Try primary model with backoff
    for attempt in range(4):
        try:
            content = llm.invoke(prompt).content
            if content:  # Guard against empty completions
                return content
            logger.warning("Empty response from %s (attempt %d); retrying...", model, attempt + 1)
            time.sleep(5)
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
            content = fallback.invoke(prompt).content
            if content:
                return content
            time.sleep(5)
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
        f"HARD DATA: {_hard_data_line(state)}\n\n"
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
        f"HARD DATA: {_hard_data_line(state)}\n\n"
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

# Judge model chain: prefer the dedicated structured model, then the broader
# model chain as structured-output fallbacks (arranged by preference).
def _judge_model_chain() -> list[str]:
    chain = [JUDGE_MODEL]
    for m in MODEL_CHAIN:
        if m not in chain:
            chain.append(m)
    return chain


def _judge_prompt(state: DebateState) -> str:
    """Build the judge prompt (shared by structured and plain-text paths)."""
    ticker = state.get("ticker", "")
    company = state.get("company_name", ticker)
    bull_case = state.get("bull_case", "")
    bear_case = state.get("bear_case", "")
    sec = state.get("sec_context", "")
    strategy = state.get("strategy", "GRAHAM CLASSIC")

    prompt = (
        f"You are the Chief Investment Officer making the final call on "
        f"{company} ({ticker}).\n\n"
        f"HARD DATA: {_hard_data_line(state)}\n\n"
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
        "Produce a structured investment memo with EXACTLY these fields:\n"
        '- "quantitative_base": Price vs calculated valuation, margin of safety math\n'
        '- "lynch_pitch": The best data-backed catalyst\n'
        '- "munger_invert": The key risk from the bear case\n'
        '- "verdict": One of STRONG BUY, BUY, WATCH, AVOID\n'
        '- "bottom_line": One sentence summary\n'
        "Return ONLY a valid JSON object with those five keys and nothing else."
    )
    return prompt


def _structured_verdict_invoke(prompt: str):
    """Try the judge models for a structured InvestmentVerdict with retries.

    Returns an ``InvestmentVerdict`` or ``None`` if every model/attempt fails.
    Empty (None) structured responses and rate limits are retried per model;
    unavailable models are skipped via the fallback chain.
    """
    from src.models.verdict import InvestmentVerdict

    for idx, model in enumerate(_judge_model_chain()):
        try:
            llm = _make_llm(model, max_tokens=4096).with_structured_output(InvestmentVerdict)
        except Exception as exc:
            logger.warning("Model %s cannot emit structured output: %s", model, exc)
            continue

        for attempt in range(3):
            try:
                time.sleep(_DEBATE_DELAY)
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message="Pydantic serializer warnings")
                    result = llm.invoke(prompt)
                if result is not None:
                    return result
                logger.warning(
                    "Judge model %s returned empty verdict (attempt %d); retrying...",
                    model, attempt + 1,
                )
            except Exception as exc:
                err = str(exc)
                if "429" in err:
                    sleep_time = 20 * (attempt + 1)
                    logger.warning("Rate limit on judge model %s; sleeping %ds...", model, sleep_time)
                    time.sleep(sleep_time)
                elif "404" in err or "too short" in err or "does not have" in err:
                    logger.warning("Judge model %s unavailable, moving to fallback", model)
                    break  # move to next model in chain
                else:
                    logger.warning("Judge model %s error (attempt %d): %s", model, attempt + 1, exc)
                    time.sleep(5)

    return None


def _verdict_from_plain_json(prompt: str):
    """Last-resort judge: ask any surviving model for pure JSON and parse it.

    Returns an ``InvestmentVerdict`` or ``None`` if parsing fails.
    """
    from src.models.verdict import InvestmentVerdict

    json_prompt = (
        prompt
        + "\n\nIMPORTANT: Respond with ONLY a single JSON object — no markdown "
        + "fences, no commentary. Example shape: "
        + '{"quantitative_base": "...", "lynch_pitch": "...", "munger_invert": "...", '
        + '"verdict": "WATCH", "bottom_line": "..."}.'
    )

    last_error = None
    for model in _judge_model_chain():
        try:
            time.sleep(_DEBATE_DELAY)
            raw = _make_llm(model, max_tokens=4096).invoke(json_prompt).content
        except Exception as exc:
            last_error = exc
            logger.warning("Plain-JSON judge model %s failed: %s", model, exc)
            continue
        if not raw:
            continue

        # Strip markdown fences if the model wrapped the JSON anyway.
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            data = json.loads(cleaned)
            return InvestmentVerdict(**data)
        except Exception as exc:
            last_error = exc
            logger.warning("Could not parse judge JSON from %s: %s", model, exc)

    logger.error("All plain-JSON judge attempts failed: %s", last_error)
    return None


def judge_node(state: DebateState) -> dict:
    """Synthesise the debate into a structured InvestmentVerdict."""
    from src.models.verdict import InvestmentVerdict

    ticker = state.get("ticker", "")
    strategy = state.get("strategy", "GRAHAM CLASSIC")
    prompt = _judge_prompt(state)

    result = _structured_verdict_invoke(prompt)

    if result is None:
        logger.warning("All structured judge models failed for %s; trying plain-JSON...", ticker)
        result = _verdict_from_plain_json(prompt)

    if result is None:
        raise RuntimeError(
            f"Judge produced no verdict for {ticker} after structured + plain-JSON fallbacks."
        )

    verdict_text = result.to_report()
    logger.info("Judge delivered verdict for %s: %s (%s)", ticker, result.verdict, strategy)

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
    currency: str = "USD",
    market_cap: float = 0.0,
    revenue: float = 0.0,
    revenue_growth: float = 0.0,
    total_cash: float = 0.0,
    total_debt: float = 0.0,
    current_ratio: float = 0.0,
    cash_per_share: float = 0.0,
    enterprise_value: float = 0.0,
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
        "currency": currency,
        "market_cap": market_cap,
        "revenue": revenue,
        "revenue_growth": revenue_growth,
        "total_cash": total_cash,
        "total_debt": total_debt,
        "current_ratio": current_ratio,
        "cash_per_share": cash_per_share,
        "enterprise_value": enterprise_value,
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
