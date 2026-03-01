"""Interactive Chainlit agent pipeline.

Supports: manual ticker lookup, auto-scan, social scout, chat mode.
Uses the shared core modules and discovery pipeline.
"""

import io
import gc
import random
import time
import yfinance as yf
import matplotlib.pyplot as plt
from langgraph.graph import StateGraph, END

from src.llm import get_llm
from src.finance_tools import (
    check_financial_health,
    get_insider_sentiment,
    get_company_news,
    get_basic_financials,
)
from src.portfolio_tracker import record_paper_trade

from src.core.logger import get_logger
from src.core.search import brave_search
from src.core.ticker_utils import extract_tickers, resolve_ticker_suffix, normalize_price
from src.core.memory import load_seen_tickers, mark_ticker_seen
from src.core.state import AgentState

from src.discovery.screener import screen_microcaps, get_trending_tickers_from_brave
from src.discovery.scoring import rank_candidates
from src.discovery.insider_feed import get_insider_buys

logger = get_logger(__name__)

# Re-export for backward compatibility (app.py imports this)
brave_market_search = brave_search

# --- CONFIGURATION ---
MAX_MARKET_CAP = 300_000_000
MIN_MARKET_CAP = 10_000_000
MAX_PRICE_PER_SHARE = 30.00
MAX_RETRIES = 1


def generate_chart(ticker: str) -> bytes | None:
    """Generate a 6-month price chart in memory."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="6mo")
        if hist.empty:
            return None
        plt.figure(figsize=(6, 4))
        plt.plot(hist.index, hist["Close"], color="#00a1ff", linewidth=1.5)
        plt.title(f"{ticker} - 6 Month Price Action")
        plt.grid(True, linestyle="--", alpha=0.6)
        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight")
        buf.seek(0)
        plt.close("all")
        return buf.getvalue()
    except Exception as exc:
        logger.warning("Chart generation failed for %s: %s", ticker, exc)
        return None


# --- NODES ---

def chat_node(state):
    """Dedicated UI chat node for conversational queries."""
    user_query = state.get("ticker", "")
    llm = get_llm()

    prompt = f"""
    You are the Senior Broker AI for PrimoGreedy. Your team member just asked you this question:
    "{user_query}"
    
    Answer them directly, professionally, and concisely. If they are asking about financial metrics or how you work, explain your quantitative Graham and Deep Value frameworks.
    """

    try:
        response = llm.invoke(prompt).content
    except Exception as exc:
        logger.error("Chat LLM error: %s", exc)
        response = "I am experiencing issues right now. Please try again."

    return {"final_report": response, "status": "CHAT"}


def scout_node(state):
    """Scout node: manual ticker pass-through or screener+Brave discovery."""
    region = state.get("region", "USA")
    retries = state.get("retry_count", 0)

    # Manual single-ticker lookup
    is_manual = bool(state.get("ticker") and state["ticker"] != "NONE" and retries == 0)
    if is_manual:
        return {
            "ticker": resolve_ticker_suffix(state["ticker"], region),
            "manual_search": True,
        }

    # Auto-scan: use screener + Brave trending
    if retries > 0:
        time.sleep(2)

    logger.info("Auto-scanning %s for micro-caps...", region)

    trending = get_trending_tickers_from_brave(region)
    screened = screen_microcaps(region=region, extra_tickers=trending, max_results=15)

    if not screened:
        return {"ticker": "NONE", "manual_search": False}

    seen = load_seen_tickers()
    fresh = [c for c in screened if c["ticker"] not in seen]

    if not fresh:
        return {"ticker": "NONE", "manual_search": False}

    ranked = rank_candidates(fresh, top_n=3)
    best = ranked[0]

    ticker = best["ticker"]
    mark_ticker_seen(ticker)
    logger.info("Auto-scan target: %s (score=%d)", ticker, best.get("score", 0))

    return {"ticker": ticker, "manual_search": False}


def gatekeeper_node(state):
    """Validate candidate with financial health checks."""
    ticker = state.get("ticker", "NONE")
    retries = state.get("retry_count", 0)

    if ticker == "NONE":
        return {
            "is_small_cap": False,
            "status": "FAIL",
            "retry_count": retries + 1,
            "financial_data": {"reason": "Scout found no readable ticker."},
        }

    mark_ticker_seen(ticker)

    try:
        stock = yf.Ticker(ticker)
        raw_info = stock.info

        lean_info = {
            "currentPrice": raw_info.get("currentPrice", 0) or raw_info.get("regularMarketPrice", 0),
            "trailingEps": raw_info.get("trailingEps", 0),
            "bookValue": raw_info.get("bookValue", 0),
            "marketCap": raw_info.get("marketCap", 0),
            "ebitda": raw_info.get("ebitda", 0),
            "sector": raw_info.get("sector", "Unknown"),
            "freeCashflow": raw_info.get("freeCashflow", 0),
            "totalCash": raw_info.get("totalCash", 0),
            "currency": raw_info.get("currency", "USD"),
        }
        del raw_info
        gc.collect()

        price = normalize_price(lean_info["currentPrice"], ticker, lean_info["currency"])
        lean_info["currentPrice"] = price
        mkt_cap = lean_info["marketCap"]

        chart_bytes = generate_chart(ticker)

        if price > MAX_PRICE_PER_SHARE:
            return {
                "market_cap": mkt_cap,
                "is_small_cap": False,
                "status": "FAIL",
                "company_name": ticker,
                "financial_data": lean_info,
                "retry_count": retries + 1,
                "final_report": f"Price ${price:.2f} exceeds ${MAX_PRICE_PER_SHARE} limit.",
                "chart_data": chart_bytes,
            }

        if not (MIN_MARKET_CAP < mkt_cap < MAX_MARKET_CAP):
            return {
                "market_cap": mkt_cap,
                "is_small_cap": False,
                "status": "FAIL",
                "company_name": ticker,
                "financial_data": lean_info,
                "retry_count": retries + 1,
                "final_report": f"Market Cap ${mkt_cap:,.0f} is outside the $10M-$300M range.",
                "chart_data": chart_bytes,
            }

        health = check_financial_health(ticker, lean_info)
        if health["status"] == "FAIL":
            return {
                "market_cap": mkt_cap,
                "is_small_cap": False,
                "status": "FAIL",
                "company_name": ticker,
                "financial_data": lean_info,
                "retry_count": retries + 1,
                "final_report": f"**GATEKEEPER REJECT:** {health['reason']}",
                "chart_data": chart_bytes,
            }

        return {
            "market_cap": mkt_cap,
            "is_small_cap": True,
            "status": "PASS",
            "company_name": stock.info.get("shortName", ticker),
            "financial_data": lean_info,
            "chart_data": chart_bytes,
        }

    except Exception as exc:
        logger.error("Gatekeeper error for %s: %s", ticker, exc)
        return {
            "is_small_cap": False,
            "status": "FAIL",
            "retry_count": retries + 1,
            "financial_data": {"reason": f"API Error: {exc}"},
        }


def analyst_node(state):
    """Senior Broker analysis with Graham Number + Finnhub + insider data."""
    ticker = state["ticker"]
    info = state.get("financial_data", {})
    chart_bytes = state.get("chart_data")
    region = state.get("region", "USA")
    llm = get_llm()

    if state.get("status") == "FAIL":
        reason = state.get("final_report", info.get("reason", "Failed basic criteria."))
        verdict = (
            f"### REJECTED BY GATEKEEPER\n"
            f"**Reason:** {reason}\n\n"
            f"*The data for {ticker} was retrieved, but it does not fit the PrimoGreedy small-cap profile.*"
        )
        return {"final_verdict": verdict, "final_report": verdict, "chart_data": chart_bytes}

    price = info.get("currentPrice", 0) or 0
    eps = info.get("trailingEps", 0) or 0
    book_value = info.get("bookValue", 0) or 0
    ebitda = info.get("ebitda", 0) or 0
    sector = info.get("sector", "Unknown")

    if eps > 0 and book_value > 0:
        strategy = "GRAHAM VALUE"
        valuation = (22.5 * eps * book_value) ** 0.5
        thesis = f"Profitable in {sector}. Graham Value ${valuation:.2f} vs Price ${price:.2f}. EBITDA: ${ebitda:,.0f}."
    else:
        strategy = "DEEP VALUE ASSET PLAY"
        ratio = price / book_value if book_value > 0 else 0
        thesis = f"Unprofitable in {sector}. Trading at {ratio:.2f}x Book Value. EBITDA: ${ebitda:,.0f}."

    news = brave_search(f"{ticker} stock {sector} catalysts insider buying")

    prompt = f"""
    Act as a Senior Financial Broker evaluating {state.get('company_name')} ({ticker}).
    
    HARD DATA: Price: ${price} | EPS: {eps} | Book/Share: {book_value} | EBITDA: {ebitda}
    QUANTITATIVE THESIS: {thesis}
    """

    if region == "USA" and "." not in ticker:
        logger.info("Researching Finnhub for %s...", ticker)
        context = ""
        try:
            context += get_insider_sentiment.invoke({"ticker": ticker}) + "\n"
            context += get_company_news.invoke({"ticker": ticker}) + "\n"
            context += get_basic_financials.invoke({"ticker": ticker}) + "\n"
        except Exception as exc:
            logger.warning("Finnhub tool error for %s: %s", ticker, exc)

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
        verdict = llm.invoke(prompt).content
        record_paper_trade(ticker, price, verdict, source="Chainlit UI")
    except Exception as exc:
        logger.error("LLM analysis failed for %s: %s", ticker, exc)
        verdict = f"Strategy: {strategy}\nLLM analysis unavailable: {exc}"

    return {"final_verdict": verdict, "final_report": verdict, "chart_data": chart_bytes}


# --- GRAPH ---

workflow = StateGraph(AgentState)
workflow.add_node("chat", chat_node)
workflow.add_node("scout", scout_node)
workflow.add_node("gatekeeper", gatekeeper_node)
workflow.add_node("analyst", analyst_node)


def initial_routing(state):
    """Direct traffic: spaces -> chat, otherwise -> scout."""
    query = str(state.get("ticker", ""))
    if " " in query:
        return "chat"
    return "scout"


workflow.set_conditional_entry_point(
    initial_routing,
    {"chat": "chat", "scout": "scout"},
)


def check_status(state):
    if state.get("manual_search"):
        return "analyst"
    if state.get("status") == "PASS":
        return "analyst"
    if state.get("retry_count", 0) >= MAX_RETRIES:
        return "analyst"
    return "scout"


workflow.add_edge("chat", END)
workflow.add_edge("scout", "gatekeeper")
workflow.add_conditional_edges(
    "gatekeeper",
    check_status,
    {"analyst": "analyst", "scout": "scout"},
)
workflow.add_edge("analyst", END)

app = workflow.compile()
