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
MIN_MARKET_CAP = 10_000_000    # Min: $10 Million
MAX_PRICE_PER_SHARE = 30.00    # NEW: Must be under $30
MAX_RETRIES = 1                # 1 Retry per region (Total 2 attempts)
HARD_TIMEOUT_SECONDS = 3000    # 50 minutes to match GitHub Actions

# 🌍 EXCHANGE SUFFIX MAP
REGION_SUFFIXES = {
    "USA":       [""],
    "UK":        [".L"],
    "Canada":    [".TO", ".V"],
    "Australia": [".AX"],
}

# --- TIMEOUT HANDLER ---
def _timeout_handler(signum, frame):
    raise TimeoutError("⏰ Hard timeout reached (50 minutes). Aborting.")

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

# --- HELPER FUNCTIONS ---
def extract_ticker_from_text(text: str) -> str:
    cleaned = text.strip().upper()
    if re.fullmatch(r'[A-Z]{1,5}(\.[A-Z]{1,2})?', cleaned): return cleaned
    candidates = re.findall(r'\b([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\b', cleaned)
    noise_words = {"THE", "AND", "FOR", "ARE", "NOT", "YOU", "ALL", "CAN", "ONE", "OUT", "HAS", "NEW", "NOW", "SEE", "WHO", "GET", "SHE", "TOO", "USE", "NONE", "THIS", "THAT", "WITH", "HAVE", "FROM", "THEY", "BEEN", "SAID", "MAKE", "LIKE", "JUST", "OVER", "SUCH", "TAKE", "YEAR", "SOME", "MOST", "VERY", "WHEN", "WHAT", "YOUR", "ALSO", "INTO", "ROLE", "TASK", "INPUT", "STOCK", "TICKER", "CAP", "MICRO", "NANO"}
    candidates = [c for c in candidates if c not in noise_words and len(c) >= 2]
    return candidates[0] if candidates else "NONE"

def resolve_ticker_suffix(raw_ticker: str, region: str) -> str:
    if "." in raw_ticker: return raw_ticker
    suffixes = REGION_SUFFIXES.get(region, [""])
    if suffixes == [""]: return raw_ticker
    
    for suffix in suffixes:
        candidate = f"{raw_ticker}{suffix}"
        try:
            info = yf.Ticker(candidate).info
            if info.get('marketCap', 0) > 0:
                print(f"   🌍 Suffix resolved: {raw_ticker} → {candidate}")
                return candidate
        except: pass
    return raw_ticker

# --- 3. THE WORKERS ---

def scout_node(state):
    region = state.get('region', 'USA')
    retries = state.get('retry_count', 0)
    
    if retries > 0:
        print(f"   🔄 Retry pause (2s)...")
        time.sleep(2)

    print(f"\n🔭 [Attempt {retries+1}/{MAX_RETRIES+1}] Scouting {region} Micro-Caps...")
    
    base_queries = [
        f"best value stocks {region} under $30 per share",
        f"undervalued stocks {region} price below 30 dollars",
        f"hidden gem microcap stocks {region} with low float",
        f"benjamin graham net net stocks {region} cheap share price",
        f"insider buying microcap stocks {region} this week"
    ]
    
    try:
        search_results = brave_market_search(random.choice(base_queries))
    except Exception as e:
        print(f"   ❌ Search Error: {e}")
        return {"ticker": "NONE"}

    extraction_prompt = f"""
    ROLE: Financial Data Extractor.
    INPUT: {search_results}
    TASK: Extract the single most interesting MICRO-CAP stock ticker.
    CONSTRAINT: Must be listed in {region}. Ignore companies larger than $300M.
    OUTPUT: Just the ticker symbol. Nothing else.
    """
    
    try:
        if llm:
            raw = llm.invoke(extraction_prompt).content.strip()
            ticker = extract_ticker_from_text(raw)
            print(f"   🎯 Target: {ticker}")
            if ticker != "NONE": ticker = resolve_ticker_suffix(ticker, region)
            return {"ticker": ticker}
        else: return {"ticker": "NONE"}
    except: return {"ticker": "NONE"}

def gatekeeper_node(state):
    ticker = state['ticker']
    retries = state.get('retry_count', 0)
    
    if ticker == "NONE":
        print(f"   🚫 No ticker found.")
        return {"is_small_cap": False, "retry_count": retries + 1}

    print(f"   ⚖️ Weighing {ticker}...")
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        mkt_cap = info.get('marketCap', 0)
        price = info.get('currentPrice', 0) or info.get('regularMarketPrice', 0)
        name = info.get('shortName', ticker)
        
        # 1. Price Check
        if price > MAX_PRICE_PER_SHARE:
             print(f"   🚫 {ticker} Rejected — Price ${price} > ${MAX_PRICE_PER_SHARE}.")
             return {"is_small_cap": False, "retry_count": retries + 1}

        # 2. Market Cap Check
        if not (MIN_MARKET_CAP < mkt_cap < MAX_MARKET_CAP):
            print(f"   🚫 {ticker} Rejected — Cap ${mkt_cap:,.0f} out of range.")
            return {"is_small_cap": False, "retry_count": retries + 1}

        print(f"   ✅ {ticker} Passed Gatekeeper (Price: ${price} | Cap: ${mkt_cap:,.0f})")
        
        # We pass the full info dictionary to the Analyst so it can do the Two-Path logic
        return {
            "market_cap": mkt_cap, 
            "is_small_cap": True, 
            "company_name": name, 
            "financial_data": info
        }
    except Exception as e:
        print(f"   ❌ YFinance Error for {ticker}: {e}")
        return {"is_small_cap": False, "retry_count": retries + 1}

def analyst_node(state):
    """
    🧠 THE SENIOR BROKER
    Evaluates Profitable stocks using Graham Number.
    Evaluates Unprofitable stocks (Miners/Biotech) using Asset Value.
    """
    ticker = state['ticker']
    info = state.get('financial_data', {})
    print(f"   🧠 Analyzing {ticker}...")
    
    # Extract Key Metrics
    price = info.get('currentPrice', 0) or info.get('regularMarketPrice', 0)
    eps = info.get('trailingEps', 0)
    book_value = info.get('bookValue', 0)
    
    # SENIOR BROKER LOGIC
    if eps > 0 and book_value > 0:
        strategy = "GRAHAM CLASSIC"
        valuation = (22.5 * eps * book_value) ** 0.5
        thesis = f"Profitable. Graham Value ${valuation:.2f} vs Price ${price:.2f}."
    else:
        strategy = "DEEP VALUE ASSET PLAY"
        valuation = book_value
        ratio = price / book_value if book_value > 0 else 0
        thesis = f"Unprofitable Miner/Turnaround. Trading at {ratio:.2f}x Book Value. Assets are the safety net."

    news = brave_market_search(f"{ticker} stock analysis catalysts")
    
    prompt = f"""
    Act as a Senior Financial Broker. Analyze {state.get('company_name', ticker)} ({ticker}).
    
    STRATEGY: {strategy}
    DATA: Price: ${price} | EPS: {eps} | Book Value/Share: {book_value}
    CONTEXT: {thesis}
    NEWS: {news}
    
    DECISION LOGIC:
    1. If Strategy is GRAHAM: Is it cheap relative to earnings?
    2. If Strategy is ASSET PLAY: Is the company going bankrupt, or is the land/cash real?
    
    OUTPUT:
    VERDICT: STRONG BUY / BUY / WATCH / AVOID
    RATIONALE: Max 3 sentences weighing Valuation vs News.
    """
    
    if llm:
        verdict = llm.invoke([SystemMessage(content="You are a skeptical Value Investor."), HumanMessage(content=prompt)]).content
    else: 
        verdict = "No AI Analysis available."
    
    return {"final_verdict": verdict}

def email_node(state):
    region = state.get('region', 'Global')
    ticker = state.get('ticker', 'Unknown')
    verdict = state.get('final_verdict', 'No Verdict')
    
    if not state.get('is_small_cap'):
        print(f"   ⚠️ Sending Failure Report for {region}...")
        subject = f"❌ Hunt Failed: {region}"
        body = f"Found no suitable Micro-Caps under ${MAX_PRICE_PER_SHARE} in {region} after {MAX_RETRIES+1} attempts."
    else:
        print(f"   📨 Sending Analysis for {ticker}...")
        subject = f"🧬 Micro-Cap Found ({region}): {ticker}"
        body = f"<h1>{ticker}</h1><h3>Cap: ${state.get('market_cap',0):,.0f}</h3><hr>{verdict.replace(chr(10), '<br>')}"

    # 👥 THE FULL TEAM
    team = [
        {"name": "Cisco", "email": os.getenv("EMAIL_CISCO"), "key": os.getenv("RESEND_API_KEY_CISCO")},
        {"name": "Raul",  "email": os.getenv("EMAIL_RAUL"),  "key": os.getenv("RESEND_API_KEY_RAUL")},
        {"name": "David", "email": os.getenv("EMAIL_DAVID"), "key": os.getenv("RESEND_API_KEY_DAVID")}
    ]
    
    for member in team:
        if member["email"] and member["key"]:
            try:
                send_email_report(subject, body, member["email"], member["key"])
            except: pass
    return {}

# --- 4. THE GRAPH ---
workflow = StateGraph(AgentState)
workflow.add_node("scout", scout_node)
workflow.add_node("gatekeeper", gatekeeper_node)
workflow.add_node("analyst", analyst_node)
workflow.add_node("email", email_node)
workflow.set_entry_point("scout")

def check_status(state):
    if state.get('is_small_cap'): return "analyst"
    if state.get('retry_count', 0) > MAX_RETRIES: return "email"
    return "scout"

workflow.add_edge("scout", "gatekeeper")
workflow.add_conditional_edges("gatekeeper", check_status, {"analyst": "analyst", "scout": "scout", "email": "email"})
workflow.add_edge("analyst", "email")
workflow.add_edge("email", END)
app = workflow.compile()

# 🟢 EXECUTION BLOCK (GLOBAL MARATHON MODE)
if __name__ == "__main__":
    print("🚀 Starting Global Micro-Cap Hunter (Senior Broker Edition)...")
    
    try:
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(HARD_TIMEOUT_SECONDS)
        print(f"⏰ Timeout set: {HARD_TIMEOUT_SECONDS}s")
    except: pass

    # 🌍 RUN ALL 4 REGIONS
    regions = ["USA", "UK", "Canada", "Australia"]
    
    for market in regions:
        print(f"\n--- 🏁 Initiating Hunt for {market} ---")
        try:
            app.invoke({"region": market, "retry_count": 0, "ticker": ""})
            print(f"✅ {market} Hunt Complete.")
            time.sleep(5) 
        except Exception as e:
            print(f"❌ Error in {market}: {e}")

    print("\n🎉 Global Mission Complete.")