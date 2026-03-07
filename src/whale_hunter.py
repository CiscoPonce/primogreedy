"""Daily automated micro-cap hunter (GitHub Actions cron).

Pipeline per region:  Scout -> Gatekeeper -> Analyst -> Email

An outer orchestrator graph dispatches all regions in parallel via the
LangGraph ``Send`` API, then collects results.

The Scout uses a two-pronged discovery approach:
  1. yFinance screener for systematic micro-cap filtering
  2. Brave Search for trending/momentum signals
  3. Quantitative scoring to pick the best candidate

Both feeds are merged, scored, and only the top candidate proceeds to
the expensive LLM analyst step.
"""

import operator
import os
import signal
import time
import warnings
from typing import Annotated, Literal, TypedDict

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, RetryPolicy, Send

from src.llm import get_llm, get_structured_llm, invoke_with_fallback
from src.finance_tools import (
    check_financial_health,
    get_insider_sentiment,
    get_company_news,
    get_basic_financials,
)
from src.portfolio_tracker import record_paper_trade
from src.email_utils import send_email_report

from src.core.logger import get_logger
from src.core.search import brave_search
from src.core.ticker_utils import normalize_price, REGION_SUFFIXES
from src.core.memory import load_seen_tickers, mark_ticker_seen
from src.core.state import AgentState

from src.discovery.screener import screen_microcaps, get_trending_tickers_from_brave
from src.discovery.scoring import rank_candidates
from src.discovery.insider_feed import get_insider_buys

logger = get_logger(__name__)

# --- CONFIGURATION ---
MAX_MARKET_CAP = 500_000_000
MIN_MARKET_CAP = 5_000_000
MAX_PRICE_PER_SHARE = 30.00
MAX_RETRIES = 3
HARD_TIMEOUT_SECONDS = 3000  # 50 min to match GitHub Actions


def _timeout_handler(signum, frame):
    raise TimeoutError("Hard timeout reached (50 minutes). Aborting.")


# --- NODES ---

def scout_node(state):
    """Two-pronged discovery: yFinance screener + Brave trending, then score."""
    region = state.get("region", "USA")
    retries = state.get("retry_count", 0)
    candidates_queue = state.get("candidates", [])
    seen = load_seen_tickers()

    # If we still have scored candidates from a previous scout pass, pop next
    while candidates_queue:
        next_pick = candidates_queue.pop(0)
        ticker = next_pick if isinstance(next_pick, str) else next_pick.get("ticker", "")
        if ticker and ticker not in seen:
            logger.info("Popping next candidate from queue: %s (%d left)", ticker, len(candidates_queue))
            mark_ticker_seen(ticker)
            return {"ticker": ticker, "candidates": candidates_queue}
        logger.info("Skipping %s (seen recently)", ticker)

    # Queue is empty — run full discovery
    if retries > 0:
        logger.info("Retry pause (2s)...")
        time.sleep(2)

    logger.info("[Attempt %d/%d] Scouting %s micro-caps...", retries + 1, MAX_RETRIES + 1, region)

    # Prong 1: Brave Search for trending tickers
    trending_tickers = get_trending_tickers_from_brave(region)
    logger.info("Brave trending returned %d tickers", len(trending_tickers))

    # Prong 2: yFinance screener (merges trending tickers into the pool)
    screened = screen_microcaps(region=region, extra_tickers=trending_tickers, max_results=20)

    if not screened:
        logger.warning("No candidates passed screener for %s", region)
        return {"ticker": "NONE", "candidates": []}

    # Filter out already-seen tickers
    fresh = [c for c in screened if c["ticker"] not in seen]
    if not fresh:
        logger.warning("All screened candidates already seen for %s", region)
        return {"ticker": "NONE", "candidates": []}

    # Score and rank
    ranked = rank_candidates(fresh, top_n=5)

    # Pop the best one, store rest in queue
    best = ranked[0]
    rest = [c["ticker"] for c in ranked[1:]]

    ticker = best["ticker"]
    mark_ticker_seen(ticker)
    logger.info(
        "Target acquired: %s (score=%d) | %d backups queued",
        ticker, best.get("score", 0), len(rest),
    )

    return {"ticker": ticker, "candidates": rest}


def gatekeeper_node(state) -> Command[Literal["analyst", "scout", "email"]]:
    """Validate the candidate against hard financial criteria. Routes via Command."""
    import yfinance as yf

    ticker = state.get("ticker", "NONE")
    retries = state.get("retry_count", 0)

    def _fail_route(new_retries: int) -> str:
        if new_retries > MAX_RETRIES:
            return "email"
        return "scout"

    if ticker == "NONE":
        logger.warning("No ticker to evaluate")
        update = {"is_small_cap": False, "retry_count": retries + 1}
        return Command(update=update, goto=_fail_route(retries + 1))

    logger.info("Gatekeeper evaluating %s...", ticker)
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        mkt_cap = info.get("marketCap", 0) or 0
        price = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0
        name = info.get("shortName", ticker)
        currency = info.get("currency", "USD")

        price = normalize_price(price, ticker, currency)

        if price > MAX_PRICE_PER_SHARE:
            logger.info("%s rejected — price $%.2f > $%.2f", ticker, price, MAX_PRICE_PER_SHARE)
            update = {"is_small_cap": False, "retry_count": retries + 1}
            return Command(update=update, goto=_fail_route(retries + 1))

        if not (MIN_MARKET_CAP < mkt_cap < MAX_MARKET_CAP):
            logger.info("%s rejected — cap $%s out of range", ticker, f"{mkt_cap:,.0f}")
            update = {"is_small_cap": False, "retry_count": retries + 1}
            return Command(update=update, goto=_fail_route(retries + 1))

        health = check_financial_health(ticker, info)
        if health["status"] == "FAIL":
            logger.info("%s rejected — %s", ticker, health["reason"])
            update = {"is_small_cap": False, "retry_count": retries + 1}
            return Command(update=update, goto=_fail_route(retries + 1))

        sector = health["metrics"].get("sector", "N/A")
        logger.info(
            "%s PASSED gatekeeper (price=$%.2f | cap=$%s | sector=%s)",
            ticker, price, f"{mkt_cap:,.0f}", sector,
        )

        update = {
            "market_cap": mkt_cap,
            "is_small_cap": True,
            "company_name": name,
            "financial_data": info,
        }
        return Command(update=update, goto="analyst")

    except Exception as exc:
        logger.error("yFinance error for %s: %s", ticker, exc)
        update = {"is_small_cap": False, "retry_count": retries + 1}
        return Command(update=update, goto=_fail_route(retries + 1))


def analyst_node(state):
    """Senior Broker analysis with Graham Number, Finnhub data, and insider signals."""
    ticker = state["ticker"]
    info = state.get("financial_data", {})
    region = state.get("region", "USA")

    logger.info("Analysing %s...", ticker)

    price = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0
    eps = info.get("trailingEps", 0) or 0
    book_value = info.get("bookValue", 0) or 0

    if eps > 0 and book_value > 0:
        strategy = "GRAHAM CLASSIC"
        valuation = (22.5 * eps * book_value) ** 0.5
        thesis = f"Profitable. Graham Value ${valuation:.2f} vs Price ${price:.2f}."
    else:
        strategy = "DEEP VALUE ASSET PLAY"
        valuation = book_value
        ratio = price / book_value if book_value > 0 else 0
        thesis = f"Unprofitable Miner/Turnaround. Trading at {ratio:.2f}x Book Value."

    # Gather context
    news = brave_search(f"{ticker} stock analysis catalysts")

    # SEC EDGAR ground truth (US equities only)
    sec_context = ""
    if region == "USA" and "." not in ticker:
        from src.sec_edgar import get_sec_filings
        try:
            sec_context = get_sec_filings.invoke({"ticker": ticker})
        except Exception as exc:
            logger.warning("SEC EDGAR failed for %s: %s", ticker, exc)

    # Build deep-fundamentals context
    deep_fundamentals = ""
    if region == "USA" and "." not in ticker:
        logger.info("Researching Finnhub databases for %s...", ticker)
        context = ""
        try:
            context += get_insider_sentiment.invoke({"ticker": ticker}) + "\n"
            context += get_company_news.invoke({"ticker": ticker}) + "\n"
            context += get_basic_financials.invoke({"ticker": ticker}) + "\n"
        except Exception as exc:
            logger.warning("Finnhub tool error for %s: %s", ticker, exc)

        insider = get_insider_buys(ticker)
        context += f"\nInsider Sentiment (6mo): {insider['sentiment']} | MSPR: {insider['mspr']} | Net Shares: {insider['change']}\n"
        deep_fundamentals = f"DEEP FUNDAMENTALS (FINNHUB + INSIDER FEED):\n{context}"
    else:
        deep_fundamentals = f"NEWS: {str(news)[:1500]}"

    # --- Debate or single-LLM path ---
    from src.agents.debate import is_debate_enabled, run_debate
    from src.models.kelly import get_kelly_stats, calculate_position_size

    if is_debate_enabled():
        logger.info("Running multi-agent debate for %s...", ticker)
        try:
            debate_result = run_debate(
                ticker=ticker,
                company_name=state.get("company_name", ticker),
                financial_data_summary=str(info)[:2000],
                deep_fundamentals=deep_fundamentals,
                sec_context=sec_context,
                strategy=strategy,
                price=price,
                eps=eps,
                book_value=book_value,
                ebitda=info.get("ebitda", 0) or 0,
            )
            result = debate_result["_structured_result"]

            stats = get_kelly_stats()
            result.position_size = calculate_position_size(stats, result.verdict)
            result.kelly_win_rate = stats.win_rate
            result.kelly_total_trades = stats.total_trades

            verdict = result.to_report()
            record_paper_trade(ticker, price, verdict, source="Morning Cron",
                               structured_verdict=result.verdict,
                               position_size=result.position_size)

            return {
                "final_verdict": verdict, "debate_used": True,
                "bull_case": debate_result.get("bull_case", ""),
                "bear_case": debate_result.get("bear_case", ""),
            }
        except Exception as exc:
            logger.warning("Debate failed for %s, falling back to single-LLM: %s", ticker, exc)

    # --- Single-LLM path (default or debate fallback) ---
    prompt = f"""
    Act as a Senior Financial Broker evaluating {state.get('company_name', ticker)} ({ticker}).
    
    HARD DATA: Price: ${price} | EPS: {eps} | Book/Share: {book_value} | EBITDA: {info.get('ebitda', 0)}
    QUANTITATIVE THESIS: {thesis}
    """

    if sec_context:
        prompt += f"\n{sec_context}\n"

    prompt += f"\n{deep_fundamentals}\n"

    prompt += f"""
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

    try:
        import warnings
        from src.models.verdict import InvestmentVerdict

        structured_llm = get_structured_llm().with_structured_output(InvestmentVerdict)
        result = structured_llm.invoke(prompt)

        stats = get_kelly_stats()
        result.position_size = calculate_position_size(stats, result.verdict)
        result.kelly_win_rate = stats.win_rate
        result.kelly_total_trades = stats.total_trades

        verdict = result.to_report()
        record_paper_trade(ticker, price, verdict, source="Morning Cron",
                           structured_verdict=result.verdict,
                           position_size=result.position_size)
    except Exception as exc:
        logger.warning("Structured output failed for %s, falling back to plain LLM: %s", ticker, exc)
        try:
            verdict = invoke_with_fallback(prompt)
            stats = get_kelly_stats()
            v_upper = verdict.upper()
            verdict_type = "AVOID"
            if "STRONG BUY" in v_upper:
                verdict_type = "STRONG BUY"
            elif "BUY" in v_upper:
                verdict_type = "BUY"
            elif "WATCH" in v_upper:
                verdict_type = "WATCH"
            pos = calculate_position_size(stats, verdict_type)
            if pos > 0:
                verdict += (
                    f"\n\n### POSITION SIZING (Kelly Criterion)\n"
                    f"**Recommended allocation: {pos:.1f}% of portfolio**"
                )
            record_paper_trade(ticker, price, verdict, source="Morning Cron",
                               position_size=pos)
        except Exception as exc2:
            logger.error("LLM analysis failed for %s: %s", ticker, exc2)
            verdict = f"LLM analysis unavailable: {exc2}"

    return {"final_verdict": verdict}


def email_node(state):
    """Send the analysis or failure report to the team."""
    region = state.get("region", "Global")
    ticker = state.get("ticker", "Unknown")
    verdict = state.get("final_verdict", "No Verdict")

    if not state.get("is_small_cap"):
        logger.info("Sending failure report for %s...", region)
        subject = f"Hunt Failed: {region}"
        body = f"Found no suitable micro-caps under ${MAX_PRICE_PER_SHARE} in {region} after {MAX_RETRIES + 1} attempts."
    else:
        logger.info("Sending analysis for %s...", ticker)
        subject = f"Micro-Cap Found ({region}): {ticker}"
        body = (
            f"<h1>{ticker}</h1>"
            f"<h3>Cap: ${state.get('market_cap', 0):,.0f}</h3>"
            f"<hr>{verdict.replace(chr(10), '<br>')}"
        )

    team = [
        {"name": "Cisco", "email": os.getenv("EMAIL_CISCO"), "key": os.getenv("RESEND_API_KEY_CISCO")},
        {"name": "Raul", "email": os.getenv("EMAIL_RAUL"), "key": os.getenv("RESEND_API_KEY_RAUL")},
        {"name": "David", "email": os.getenv("EMAIL_DAVID"), "key": os.getenv("RESEND_API_KEY_DAVID")},
    ]

    for member in team:
        if member["email"] and member["key"]:
            try:
                send_email_report(subject, body, member["email"], member["key"])
            except Exception as exc:
                logger.error("Email to %s failed: %s", member["name"], exc)

    return {}


# ---------------------------------------------------------------------------
# Per-region subgraph  (scout -> gatekeeper -> analyst -> email)
# ---------------------------------------------------------------------------

_api_retry = RetryPolicy(max_attempts=3, initial_interval=2.0)

_region_workflow = StateGraph(AgentState)
_region_workflow.add_node("scout", scout_node, retry=_api_retry)
_region_workflow.add_node("gatekeeper", gatekeeper_node, retry=_api_retry)
_region_workflow.add_node("analyst", analyst_node, retry=_api_retry)
_region_workflow.add_node("email", email_node, retry=_api_retry)
_region_workflow.add_edge(START, "scout")
_region_workflow.add_edge("scout", "gatekeeper")
_region_workflow.add_edge("analyst", "email")
_region_workflow.add_edge("email", END)
_region_app = _region_workflow.compile(checkpointer=InMemorySaver())


# ---------------------------------------------------------------------------
# Orchestrator graph  (parallel fan-out via Send API)
# ---------------------------------------------------------------------------

class GlobalHunterState(TypedDict, total=False):
    regions: list[str]
    region_results: Annotated[list, operator.add]


def dispatch_regions(state: GlobalHunterState):
    """Fan-out: emit one Send per region so they run in parallel."""
    return [
        Send("hunt_region", {"region": r})
        for r in state["regions"]
    ]


def hunt_region(state) -> dict:
    """Invoke the full per-region pipeline and report back."""
    region = state.get("region", "USA")
    logger.info("--- Initiating hunt for %s ---", region)
    try:
        config = {
            "configurable": {"thread_id": f"hunt-{region.lower()}"},
            "recursion_limit": 30,
        }
        _region_app.invoke(
            {"region": region, "retry_count": 0, "ticker": ""},
            config,
        )
        logger.info("%s hunt complete.", region)
        return {"region_results": [{"region": region, "success": True}]}
    except Exception as exc:
        logger.error("Error in %s: %s", region, exc, exc_info=True)
        return {"region_results": [{"region": region, "success": False, "error": str(exc)}]}


_orchestrator = StateGraph(GlobalHunterState)
_orchestrator.add_node("hunt_region", hunt_region, retry=_api_retry)
_orchestrator.add_conditional_edges(START, dispatch_regions, ["hunt_region"])
_orchestrator.add_edge("hunt_region", END)
app = _orchestrator.compile(checkpointer=InMemorySaver())


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    catalyst_ticker = os.getenv("CATALYST_TICKER", "").strip()

    if catalyst_ticker:
        logger.info("Catalyst alert mode — analysing %s only", catalyst_ticker)
        try:
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(HARD_TIMEOUT_SECONDS)
        except (AttributeError, ValueError):
            pass

        config = {
            "configurable": {"thread_id": f"catalyst-{catalyst_ticker.lower()}"},
            "recursion_limit": 30,
        }
        _region_app.invoke(
            {"region": "USA", "retry_count": 0, "ticker": catalyst_ticker},
            config,
        )
        logger.info("Catalyst analysis complete for %s.", catalyst_ticker)
    else:
        logger.info("Starting Global Micro-Cap Hunter (Screener + Brave Edition)...")

        try:
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(HARD_TIMEOUT_SECONDS)
            logger.info("Timeout set: %ds", HARD_TIMEOUT_SECONDS)
        except (AttributeError, ValueError):
            logger.info("SIGALRM not available on this platform")

        regions = ["USA", "UK", "Canada", "Australia"]

        config = {"configurable": {"thread_id": "global-hunt"}, "recursion_limit": 30}
        result = app.invoke({"regions": regions}, config)

        for entry in result.get("region_results", []):
            status = "OK" if entry.get("success") else f"FAILED: {entry.get('error', 'unknown')}"
            logger.info("Region %s: %s", entry.get("region"), status)

        logger.info("Global mission complete.")
