import os
import re
import random
import signal
import time
from datetime import datetime, timezone
from typing import TypedDict
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage

# Import tools
import yfinance as yf
from src.llm import get_llm
from src.finance_tools import check_financial_health
from src.email_utils import send_email_report
from src.agent import brave_market_search

# --- 1. CONFIGURATION ---
MAX_MARKET_CAP = 300_000_000   # Max: $300 Million
MIN_MARKET_CAP = 10_000_000    # Min: $10 Million (Avoid shells)
MAX_RETRIES = 1                # 2 Retries = 3 Total Attempts per region
HARD_TIMEOUT_SECONDS = 240     # 4 minutes — self-kill before GitHub Actions does

# 🌍 EXCHANGE SUFFIX MAP
# yFinance requires the correct exchange suffix for non-US tickers.
# For each region we list suffixes in priority order (most common first).
REGION_SUFFIXES = {
    "USA":       [""],                # No suffix needed
    "UK":        [".L"],              # London Stock Exchange
    "Canada":    [".TO", ".V"],       # TSX, TSX Venture
    "Australia": [".AX"],             # ASX
}


# --- TIMEOUT HANDLER ---
def _timeout_handler(signum, frame):
    raise TimeoutError("⏰ Hard timeout reached (4 minutes). Aborting.")


# --- 2. THE MEMORY ---
class AgentState(TypedDict):
    region: str
    ticker: str
    company_name: str
    market_cap: float
    is_small_cap: bool
    financial_data: dict
    final_verdict: str
    retry_count: int

llm = get_llm()


# --- HELPER: Regex Ticker Extraction ---
def extract_ticker_from_text(text: str) -> str:
    """
    Extract a stock ticker from LLM output.
    Handles cases where the LLM returns prose instead of a clean symbol.
    Supports formats: LMFA, ABF.L, TSE:RY
    """
    cleaned = text.strip().upper()

    # Best case: LLM returned a clean ticker
    if re.fullmatch(r'[A-Z]{1,5}(\.[A-Z]{1,2})?', cleaned):
        return cleaned

    # Fallback: extract tickers from prose
    # Match patterns like LMFA, ABF.L but not common English words
    candidates = re.findall(r'\b([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\b', cleaned)

    # Filter out common false positives
    noise_words = {
        "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL",
        "CAN", "HER", "WAS", "ONE", "OUR", "OUT", "HAS", "HIS",
        "HOW", "ITS", "MAY", "NEW", "NOW", "OLD", "SEE", "WAY",
        "WHO", "DID", "GET", "HAS", "HIM", "LET", "SAY", "SHE",
        "TOO", "USE", "NONE", "THIS", "THAT", "WITH", "HAVE",
        "FROM", "THEY", "BEEN", "SAID", "EACH", "MAKE", "LIKE",
        "JUST", "OVER", "SUCH", "TAKE", "YEAR", "THEM", "SOME",
        "THAN", "MOST", "VERY", "WHEN", "WHAT", "YOUR", "ALSO",
        "INTO", "ROLE", "TASK", "INPUT", "STOCK", "TICKER",
        "CAP", "MICRO", "NANO",
    }
    candidates = [c for c in candidates if c not in noise_words and len(c) >= 2]

    if candidates:
        return candidates[0]

    return "NONE"


# --- HELPER: Exchange Suffix Resolution ---
def resolve_ticker_suffix(raw_ticker: str, region: str) -> str:
    """
    Append the correct exchange suffix for non-US regions.
    Tries each suffix against yFinance and returns the first one
    that yields a valid marketCap > 0.

    Examples:
        resolve_ticker_suffix("FRU", "Canada")   -> "FRU.TO" or "FRU.V"
        resolve_ticker_suffix("ABF", "UK")        -> "ABF.L"
        resolve_ticker_suffix("LMFA", "USA")      -> "LMFA"
    """
    # If the ticker already has a suffix (e.g., LLM returned "ABF.L"), keep it
    if "." in raw_ticker:
        return raw_ticker

    suffixes = REGION_SUFFIXES.get(region, [""])

    # USA — no suffix needed
    if suffixes == [""]:
        return raw_ticker

    # Try each suffix and validate against yFinance
    for suffix in suffixes:
        candidate = f"{raw_ticker}{suffix}"
        try:
            info = yf.Ticker(candidate).info
            mkt_cap = info.get('marketCap', 0)
            price = info.get('currentPrice', 0) or info.get('regularMarketPrice', 0)
            if mkt_cap and mkt_cap > 0 and price and price > 0:
                print(f"   🌍 Suffix resolved: {raw_ticker} → {candidate} (${mkt_cap:,.0f})")
                return candidate
            else:
                print(f"   ⏭️  {candidate} — no valid data, trying next suffix...")
        except Exception as e:
            print(f"   ⏭️  {candidate} — yFinance error: {e}")

    # If nothing worked, return the raw ticker (gatekeeper will reject it)
    print(f"   ⚠️ No valid suffix found for {raw_ticker} in {region}. Passing raw.")
    return raw_ticker


# --- 3. THE WORKERS ---

def scout_node(state):
    """
    🕵️‍♂️ THE SCOUT: Finds a ticker.
    """
    region = state.get('region', 'USA')
    retries = state.get('retry_count', 0)

    # Safety pause on retries (moved from check_status)
    if retries > 0:
        print(f"   🔄 Retry pause (2s)...")
        time.sleep(2)

    print(f"\n🔭 [Attempt {retries+1}/{MAX_RETRIES+1}] Scouting {region} Micro-Caps...")

    base_queries = [
        f"undervalued microcap stocks {region} under $300m market cap",
        f"profitable nano cap stocks {region} 2026",
        f"hidden gem microcap stocks {region} with low float",
        f"debt free microcap companies {region} high growth",
        f"insider buying microcap stocks {region} this week"
    ]
    query = random.choice(base_queries)

    try:
        search_results = brave_market_search(query)
    except Exception as e:
        print(f"   ❌ Search Error: {e}")
        return {"ticker": "NONE"}

    extraction_prompt = f"""
    ROLE: Financial Data Extractor.
    INPUT: {search_results}

    TASK: Extract the single most interesting MICRO-CAP stock ticker.
    CONSTRAINT: Must be listed in {region}. Ignore companies larger than $300M.

    OUTPUT: Just the ticker symbol (e.g., LMFA, ABF.L). Nothing else.
    """

    try:
        if llm:
            raw_response = llm.invoke(extraction_prompt).content.strip()
            ticker = extract_ticker_from_text(raw_response)
            print(f"   🎯 Target: {ticker} (raw: '{raw_response[:60]}')")

            # 🌍 SUFFIX ENFORCER: Fix non-US tickers
            if ticker != "NONE":
                ticker = resolve_ticker_suffix(ticker, region)

            return {"ticker": ticker}
        else:
            return {"ticker": "NONE"}
    except Exception as e:
        print(f"   ❌ LLM Error: {e}")
        return {"ticker": "NONE"}


def gatekeeper_node(state):
    """
    🛡️ THE GATEKEEPER: Hard-filters on market cap only.
    Financial health is collected as ADVISORY data for the analyst.
    (Most micro-caps are unprofitable — a hard Graham gate rejects 95% of them.)
    """
    ticker = state['ticker']
    current_retries = state.get('retry_count', 0)

    # 1. Check for Invalid Ticker
    if ticker == "NONE":
        print(f"   🚫 No valid ticker found. Incrementing retry.")
        return {
            "is_small_cap": False,
            "market_cap": 0,
            "retry_count": current_retries + 1
        }

    # 2. Check Market Cap (HARD GATE — the only strict filter)
    print(f"   ⚖️ Weighing {ticker}...")
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        mkt_cap = info.get('marketCap', 0)
        name = info.get('shortName', ticker)

        if not (MIN_MARKET_CAP < mkt_cap < MAX_MARKET_CAP):
            print(f"   🚫 {ticker} Rejected — Market Cap ${mkt_cap:,.0f} outside range. Retry.")
            return {
                "market_cap": mkt_cap,
                "is_small_cap": False,
                "company_name": name,
                "retry_count": current_retries + 1
            }

        print(f"   ✅ {ticker} Market Cap OK (${mkt_cap:,.0f})")

        # 3. Financial Health — ADVISORY ONLY (no hard rejection)
        print(f"   🧮 Collecting Graham health data for {ticker}...")
        health = check_financial_health(ticker)
        health_status = health.get('status', 'UNKNOWN')
        health_reason = health.get('reason', 'N/A')

        if health_status == 'FAIL':
            print(f"   ⚠️ {ticker} Graham flag: {health_reason} (passing to Analyst anyway)")
        else:
            print(f"   ✅ {ticker} Graham: {health_reason}")

        return {
            "market_cap": mkt_cap,
            "is_small_cap": True,
            "company_name": name,
            "financial_data": health
        }

    except Exception as e:
        print(f"   ❌ YFinance Error for {ticker}: {e}")
        return {
            "is_small_cap": False,
            "market_cap": 0,
            "retry_count": current_retries + 1
        }


def analyst_node(state):
    """
    🧠 THE ANALYST: Deep dive with LLM.
    Receives both healthy AND unhealthy stocks — must evaluate turnaround plays.
    """
    ticker = state['ticker']
    fin_data = state.get('financial_data', {})
    health_status = fin_data.get('status', 'UNKNOWN')
    metrics = fin_data.get('metrics', {})
    print(f"   🧠 Deep analysis of {ticker} (Health: {health_status})...")

    news = brave_market_search(f"{ticker} stock analysis")

    # Build health context string for the prompt
    if health_status == 'FAIL':
        health_context = f"⚠️ GRAHAM FLAG: {fin_data.get('reason')}\nThis stock FAILED traditional Graham screening. Evaluate as a potential TURNAROUND PLAY."
    else:
        health_context = f"✅ GRAHAM PASS: {fin_data.get('reason')}"

    prompt = f"""
    Analyze {state.get('company_name', ticker)} ({ticker}) as a Micro-Cap opportunity.
    Market Cap: ${state.get('market_cap', 0):,.0f}

    FINANCIAL DATA:
    {metrics}

    HEALTH CHECK:
    {health_context}

    MARKET NEWS:
    {news}

    INSTRUCTIONS:
    - Many micro-caps are pre-revenue or unprofitable. This is NORMAL.
    - For unprofitable companies, evaluate: asset base, cash runway, insider ownership,
      catalysts (e.g., drill results, FDA approvals, contract wins).
    - Use Price-to-Book if Graham Number is unavailable (negative earnings).
    - Be honest about risk but don't auto-reject turnaround plays.

    OUTPUT:
    VERDICT: BUY / WATCH / AVOID
    Thesis: 3 sentences max.
    Key Risk: 1 sentence.
    """

    if llm:
        response = llm.invoke([
            SystemMessage(content="You are a Value Investor specialising in micro-cap turnarounds."),
            HumanMessage(content=prompt)
        ])
        verdict = response.content
    else:
        verdict = f"Data: {fin_data.get('reason')}"

    return {"final_verdict": verdict}


def email_node(state):
    """
    📧 THE REPORTER: Sends Success OR Failure reports.
    """
    region = state.get('region', 'Global')
    ticker = state.get('ticker', 'Unknown')
    verdict = state.get('final_verdict', 'No Verdict')
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    # Build email content based on outcome
    if not state.get('is_small_cap'):
        print(f"   ⚠️ Sending Failure Report for {region}...")
        subject = f"🧬 Micro-Cap Hunter: No Targets Found ({region})"
        html_body = f"""
        <h1>❌ Hunt Failed: {region}</h1>
        <p>Scouted {MAX_RETRIES + 1} times but found no companies in the
        Micro-Cap range (${MIN_MARKET_CAP/1e6:.0f}M – ${MAX_MARKET_CAP/1e6:.0f}M)
        with valid market data.</p>
        <hr>
        <small>Agent: PrimoGreedy | {timestamp}</small>
        """
    else:
        print(f"   📨 Sending Analysis for {ticker}...")
        subject = f"🧬 Micro-Cap Found ({region}): {ticker}"
        html_body = f"""
        <h1>📍 Region: {region}</h1>
        <h2>Ticker: {ticker} — {state.get('company_name', '')}</h2>
        <h3>Market Cap: ${state.get('market_cap', 0):,.0f}</h3>
        <hr>
        {verdict.replace(chr(10), '<br>')}
        <hr>
        <small>Agent: PrimoGreedy | {timestamp}</small>
        """

    team = [
        {"name": "Cisco", "email": os.getenv("EMAIL_CISCO"), "key": os.getenv("RESEND_API_KEY_CISCO")},
        {"name": "Raul",  "email": os.getenv("EMAIL_RAUL"),  "key": os.getenv("RESEND_API_KEY_RAUL")},
        {"name": "David", "email": os.getenv("EMAIL_DAVID"), "key": os.getenv("RESEND_API_KEY_DAVID")}
    ]

    for member in team:
        if member["email"] and member["key"]:
            try:
                send_email_report(subject, html_body, member["email"], member["key"])
                print(f"   ✅ Email sent to {member['name']}")
            except Exception as e:
                print(f"   ❌ Email failed for {member['name']}: {e}")
        else:
            print(f"   ⚠️ Skipped {member['name']} (no credentials)")

    return {}


# --- 4. THE GRAPH ---
workflow = StateGraph(AgentState)
workflow.add_node("scout", scout_node)
workflow.add_node("gatekeeper", gatekeeper_node)
workflow.add_node("analyst", analyst_node)
workflow.add_node("email", email_node)

workflow.set_entry_point("scout")


def check_status(state):
    # 1. Found a Gem? -> Analyze
    if state.get('is_small_cap'):
        return "analyst"

    # 2. Retries exhausted? -> Email Failure Report
    if state.get('retry_count', 0) > MAX_RETRIES:
        return "email"

    # 3. Try again -> Loop back to scout
    return "scout"


workflow.add_edge("scout", "gatekeeper")
workflow.add_conditional_edges("gatekeeper", check_status,
    {"analyst": "analyst", "scout": "scout", "email": "email"}
)
workflow.add_edge("analyst", "email")
workflow.add_edge("email", END)

app = workflow.compile()


# 🟢 EXECUTION BLOCK (SNIPER MODE)
if __name__ == "__main__":
    print("🚀 Starting Micro-Cap Hunter (Sniper Mode)...")

    # Set hard timeout (Linux/macOS only — ignored on Windows)
    try:
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(HARD_TIMEOUT_SECONDS)
        print(f"⏰ Hard timeout set: {HARD_TIMEOUT_SECONDS}s")
    except AttributeError:
        print("⚠️ SIGALRM not available (Windows?). No hard timeout.")

    # 🌍 The Target List
    regions = ["USA", "UK", "Canada", "Australia"]

    # 🎲 ROULETTE: Pick ONE random market to hunt today
    target_market = random.choice(regions)

    print(f"\n--- 🎯 Today's Mission: Hunt in {target_market} ---")

    try:
        app.invoke({"region": target_market, "retry_count": 0, "ticker": ""})
        print(f"✅ Mission Complete for {target_market}.")

    except TimeoutError as e:
        print(f"\n{e}")
        print("Sending timeout notification email...")
        # Best-effort timeout email
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        team = [
            {"name": "Cisco", "email": os.getenv("EMAIL_CISCO"), "key": os.getenv("RESEND_API_KEY_CISCO")},
        ]
        for member in team:
            if member["email"] and member["key"]:
                try:
                    send_email_report(
                        f"⏰ Micro-Cap Hunter Timed Out ({target_market})",
                        f"<h1>⏰ Timeout</h1><p>The hunt for {target_market} exceeded {HARD_TIMEOUT_SECONDS}s and was aborted.</p><small>{timestamp}</small>",
                        member["email"],
                        member["key"]
                    )
                except Exception:
                    pass

    except Exception as e:
        print(f"❌ Critical Error: {e}")

    finally:
        # Cancel the alarm
        try:
            signal.alarm(0)
        except AttributeError:
            pass

    print("\n🎉 Global Mission Complete.")