"""Daily automated micro-cap hunter (GitHub Actions cron).

Pipeline:  Scout -> Gatekeeper -> Analyst -> Email

The Scout now uses a two-pronged discovery approach:
  1. yFinance screener for systematic micro-cap filtering
  2. Brave Search for trending/momentum signals
  3. Quantitative scoring to pick the best candidate

Both feeds are merged, scored, and only the top candidate proceeds to
the expensive LLM analyst step.
"""

import os
import signal
import time
from langgraph.graph import StateGraph, END

from src.llm import get_llm, invoke_with_fallback
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
MAX_MARKET_CAP = 300_000_000
MIN_MARKET_CAP = 10_000_000
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


def gatekeeper_node(state):
    """Validate the candidate against hard financial criteria."""
    import yfinance as yf

    ticker = state.get("ticker", "NONE")
    retries = state.get("retry_count", 0)

    if ticker == "NONE":
        logger.warning("No ticker to evaluate")
        return {"is_small_cap": False, "retry_count": retries + 1}

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
            return {"is_small_cap": False, "retry_count": retries + 1}

        if not (MIN_MARKET_CAP < mkt_cap < MAX_MARKET_CAP):
            logger.info("%s rejected — cap $%s out of range", ticker, f"{mkt_cap:,.0f}")
            return {"is_small_cap": False, "retry_count": retries + 1}

        health = check_financial_health(ticker, info)
        if health["status"] == "FAIL":
            logger.info("%s rejected — %s", ticker, health["reason"])
            return {"is_small_cap": False, "retry_count": retries + 1}

        sector = health["metrics"].get("sector", "N/A")
        logger.info(
            "%s PASSED gatekeeper (price=$%.2f | cap=$%s | sector=%s)",
            ticker, price, f"{mkt_cap:,.0f}", sector,
        )

        return {
            "market_cap": mkt_cap,
            "is_small_cap": True,
            "company_name": name,
            "financial_data": info,
        }

    except Exception as exc:
        logger.error("yFinance error for %s: %s", ticker, exc)
        return {"is_small_cap": False, "retry_count": retries + 1}


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

    prompt = f"""
    Act as a Senior Financial Broker evaluating {state.get('company_name', ticker)} ({ticker}).
    
    HARD DATA: Price: ${price} | EPS: {eps} | Book/Share: {book_value} | EBITDA: {info.get('ebitda', 0)}
    QUANTITATIVE THESIS: {thesis}
    """

    # Agentic tool calling for USA stocks via Finnhub + insider feed
    if region == "USA" and "." not in ticker:
        logger.info("Researching Finnhub databases for %s...", ticker)
        context = ""
        try:
            context += get_insider_sentiment.invoke({"ticker": ticker}) + "\n"
            context += get_company_news.invoke({"ticker": ticker}) + "\n"
            context += get_basic_financials.invoke({"ticker": ticker}) + "\n"
        except Exception as exc:
            logger.warning("Finnhub tool error for %s: %s", ticker, exc)

        # Proposal H: Add insider-feed data
        insider = get_insider_buys(ticker)
        context += f"\nInsider Sentiment (6mo): {insider['sentiment']} | MSPR: {insider['mspr']} | Net Shares: {insider['change']}\n"

        prompt += f"\nDEEP FUNDAMENTALS (FINNHUB + INSIDER FEED):\n{context}\n"
    else:
        prompt += f"\nNEWS: {str(news)[:1500]}\n"

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
        verdict = invoke_with_fallback(prompt)
        record_paper_trade(ticker, price, verdict, source="Morning Cron")
    except Exception as exc:
        logger.error("LLM analysis failed for %s: %s", ticker, exc)
        verdict = f"LLM analysis unavailable: {exc}"

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


# --- GRAPH ---

workflow = StateGraph(AgentState)
workflow.add_node("scout", scout_node)
workflow.add_node("gatekeeper", gatekeeper_node)
workflow.add_node("analyst", analyst_node)
workflow.add_node("email", email_node)
workflow.set_entry_point("scout")


def check_status(state):
    if state.get("is_small_cap"):
        return "analyst"
    if state.get("retry_count", 0) > MAX_RETRIES:
        return "email"
    return "scout"


workflow.add_edge("scout", "gatekeeper")
workflow.add_conditional_edges(
    "gatekeeper",
    check_status,
    {"analyst": "analyst", "scout": "scout", "email": "email"},
)
workflow.add_edge("analyst", "email")
workflow.add_edge("email", END)
app = workflow.compile()

# --- EXECUTION ---

if __name__ == "__main__":
    logger.info("Starting Global Micro-Cap Hunter (Screener + Brave Edition)...")

    try:
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(HARD_TIMEOUT_SECONDS)
        logger.info("Timeout set: %ds", HARD_TIMEOUT_SECONDS)
    except (AttributeError, ValueError):
        logger.info("SIGALRM not available on this platform")

    regions = ["USA", "UK", "Canada", "Australia"]

    for market in regions:
        logger.info("--- Initiating hunt for %s ---", market)
        try:
            app.invoke({"region": market, "retry_count": 0, "ticker": ""})
            logger.info("%s hunt complete.", market)
            time.sleep(5)
        except Exception as exc:
            logger.error("Error in %s: %s", market, exc, exc_info=True)

    logger.info("Global mission complete.")
