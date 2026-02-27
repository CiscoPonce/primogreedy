import os
import re
import random
import time
import gc
import json
import requests
import yfinance as yf
from datetime import datetime, timezone
from typing import TypedDict
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage

# Import your LLM tool
from src.llm import get_llm

# --- INLINE SEARCH TOOL ---
def brave_market_search(query: str) -> str:
    """Uses the Brave Search API to find financial news."""
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        return "No Brave API key found."
        
    headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
    try:
        response = requests.get(f"https://api.search.brave.com/res/v1/web/search?q={query}", headers=headers)
        if response.status_code == 200:
            results = response.json().get("web", {}).get("results", [])
            return "\n".join([f"- {r.get('title')}: {r.get('description')}" for r in results[:5]])
        return "Search failed."
    except Exception as e:
        return f"Search error: {str(e)}"

# --- EXCLUSION MEMORY (JSON LEDGER) ---
MEMORY_FILE = "seen_tickers.json"

def load_memory():
    if not os.path.exists(MEMORY_FILE): return {}
    try:
        with open(MEMORY_FILE, "r") as f: return json.load(f)
    except: return {}

def mark_ticker_seen(ticker):
    mem = load_memory()
    mem[ticker] = datetime.now(timezone.utc).isoformat()
    try:
        with open(MEMORY_FILE, "w") as f: json.dump(mem, f, indent=4)
    except: pass 

# --- CONFIGURATION ---
MAX_MARKET_CAP = 300_000_000
MIN_MARKET_CAP = 10_000_000
MAX_PRICE_PER_SHARE = 30.00
MAX_RETRIES = 1

REGION_SUFFIXES = {
    "USA": [""], "UK": [".L"], "Canada": [".TO", ".V"], "Australia": [".AX"]
}

# --- STATE MEMORY ---
class AgentState(TypedDict):
    region: str
    ticker: str
    company_name: str
    market_cap: float
    is_small_cap: bool
    financial_data: dict
    final_verdict: str
    retry_count: int
    status: str          
    final_report: str    
    chart_data: bytes    
    manual_search: bool  # Tracks if the user typed this in the UI

llm = get_llm()

# --- HELPERS ---
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
            if yf.Ticker(candidate).info.get('marketCap', 0) > 0:
                return candidate
        except: pass
    return raw_ticker

# --- NODES ---
def scout_node(state):
    region = state.get('region', 'USA')
    retries = state.get('retry_count', 0)
    
    # Skip scouting if manually entered in UI
    is_manual = bool(state.get('ticker') and state['ticker'] != "NONE" and retries == 0)
    if is_manual:
        return {"ticker": resolve_ticker_suffix(state['ticker'], region), "manual_search": True}

    if retries > 0: time.sleep(2)
    
    # High-Fidelity Signal Queries
    base_queries = [
        f"site:twitter.com/DeItaone OR site:twitter.com/unusual_whales breaking {region} ticker",
        f"site:twitter.com/eWhispers {region} earnings expectations",
        f"site:twitter.com/financialjuice macro {region} impact",
        f"site:twitter.com/Biotech2k1 FDA approval {region}",
        f"site:openinsider.com C-Suite buys {region} under 300m",
        f"site:finviz.com undervalued {region} low float",
        f"site:koyfin.com OR site:simplywall.st undervalued micro cap {region}"
    ]
    
    seen_list = list(load_memory().keys())
    
    try:
        search_results = brave_market_search(random.choice(base_queries))
        prompt = f"""
        Extract the single most interesting MICRO-CAP stock ticker in {region} from this text: 
        {str(search_results)[:2000]}
        
        CRITICAL: Do NOT return any of these recently analyzed tickers: {seen_list}
        Output ONLY the raw ticker symbol.
        """
        raw = llm.invoke(prompt).content
        ticker = extract_ticker_from_text(raw)
        if ticker != "NONE": ticker = resolve_ticker_suffix(ticker, region)
        return {"ticker": ticker, "manual_search": False}
    except: return {"ticker": "NONE", "manual_search": False}

def gatekeeper_node(state):
    ticker = state.get('ticker', 'NONE')
    retries = state.get('retry_count', 0)
    
    if ticker == "NONE":
        return {"is_small_cap": False, "status": "FAIL", "retry_count": retries + 1, "financial_data": {"reason": "Scout found no readable ticker."}}

    mark_ticker_seen(ticker)

    try:
        stock = yf.Ticker(ticker)
        raw_info = stock.info
        
        # 🚨 THE DATA DIET
        lean_info = {
            'currentPrice': raw_info.get('currentPrice', 0) or raw_info.get('regularMarketPrice', 0),
            'trailingEps': raw_info.get('trailingEps', 0),
            'bookValue': raw_info.get('bookValue', 0),
            'marketCap': raw_info.get('marketCap', 0),
            'ebitda': raw_info.get('ebitda', 0),
            'sector': raw_info.get('sector', 'Unknown')
        }
        del raw_info 
        gc.collect() 

        price = lean_info['currentPrice']
        mkt_cap = lean_info['marketCap']
        
        # UI Safety Soft Reject
        if price > MAX_PRICE_PER_SHARE:
            return {"market_cap": mkt_cap, "is_small_cap": False, "status": "FAIL", "company_name": stock.info.get('shortName', ticker), "financial_data": lean_info, "retry_count": retries + 1, "final_report": f"Price ${price} exceeds ${MAX_PRICE_PER_SHARE} limit."}
        
        if not (MIN_MARKET_CAP < mkt_cap < MAX_MARKET_CAP):
            return {"market_cap": mkt_cap, "is_small_cap": False, "status": "FAIL", "company_name": stock.info.get('shortName', ticker), "financial_data": lean_info, "retry_count": retries + 1, "final_report": f"Market Cap ${mkt_cap:,.0f} is outside the $10M-$300M range."}

        return {"market_cap": mkt_cap, "is_small_cap": True, "status": "PASS", "company_name": stock.info.get('shortName', ticker), "financial_data": lean_info}
    except Exception as e:
        return {"is_small_cap": False, "status": "FAIL", "retry_count": retries + 1, "financial_data": {"reason": f"API Error: {str(e)}"}}

def analyst_node(state):
    ticker = state['ticker']
    info = state.get('financial_data', {})
    
    # UI Rejection Explanation Fallback
    if state.get('status') == "FAIL":
        reason = state.get('final_report', info.get('reason', 'Failed basic criteria.'))
        verdict = f"### ❌ REJECTED BY GATEKEEPER\n**Reason:** {reason}\n\n*The data for {ticker} was retrieved, but it does not fit the PrimoGreedy small-cap profile.*"
        return {"final_verdict": verdict, "final_report": verdict}
    
    price = info.get('currentPrice', 0)
    eps = info.get('trailingEps', 0)
    book_value = info.get('bookValue', 0)
    ebitda = info.get('ebitda', 0)
    sector = info.get('sector', 'Unknown')
    
    # 🧠 SENIOR BROKER LOGIC
    if eps > 0 and book_value > 0:
        strategy = "GRAHAM VALUE"
        valuation = (22.5 * eps * book_value) ** 0.5
        thesis = f"Profitable in {sector}. Graham Value ${valuation:.2f} vs Price ${price:.2f}. EBITDA: ${ebitda:,.0f}."
    else:
        strategy = "DEEP VALUE ASSET PLAY"
        valuation = book_value
        thesis = f"Unprofitable in {sector}. Trading at {(price/book_value if book_value > 0 else 0):.2f}x Book Value. EBITDA: ${ebitda:,.0f}. Assets are the safety net."

    news = brave_market_search(f"{ticker} stock {sector} catalysts insider buying")
    
    prompt = f"""
    Act as a Senior Financial Broker evaluating {state.get('company_name')} ({ticker}).
    
    HARD DATA: Price: ${price} | EPS: {eps} | Book/Share: {book_value} | EBITDA: {ebitda}
    QUANTITATIVE THESIS: {thesis}
    NEWS: {str(news)[:1500]}
    
    Your task is to write a highly structured investment memo combining strict Graham Value math with qualitative analysis. Do not use fluff or buzzwords.
    
    Format your response EXACTLY like this:
    
    ### 🧮 THE QUANTITATIVE BASE (Graham / Asset Play)
    * State the current Price vs the calculated {strategy} valuation.
    * Briefly explain if the math supports a margin of safety.
    
    ### 🟢 THE LYNCH PITCH (Why I would own this)
    * **The Core Mechanism:** In one sentence, how does this company actually make money?
    * **The Catalyst:** Based on the news, what is the ONE simple reason this stock could run?
    
    ### 🔴 THE MUNGER INVERT (How I could lose money)
    * **Structural Weakness:** What is the most likely way an investor loses money here?
    * **The Bear Evidence:** What exact metric, news, or math would prove the bear case right?
    
    ### ⚖️ FINAL VERDICT
    STRONG BUY / BUY / WATCH / AVOID (Choose one, followed by a 1-sentence bottom line).
    """
    verdict = llm.invoke(prompt).content if llm else f"Strategy: {strategy}"
    return {"final_verdict": verdict, "final_report": verdict}

# --- GRAPH BUILDER ---
workflow = StateGraph(AgentState)
workflow.add_node("scout", scout_node)
workflow.add_node("gatekeeper", gatekeeper_node)
workflow.add_node("analyst", analyst_node)

workflow.set_entry_point("scout")

def check_status(state):
    # Route manual searches straight to the analyst to render the UI rejection
    if state.get('manual_search'): return "analyst"
    # Route successful finds to the analyst
    if state.get('status') == "PASS": return "analyst"
    # Route final failures to the analyst so emails explain what went wrong
    if state.get('retry_count', 0) >= MAX_RETRIES: return "analyst"
    return "scout"

workflow.add_edge("scout", "gatekeeper")
workflow.add_conditional_edges("gatekeeper", check_status, {"analyst": "analyst", "scout": "scout"})
workflow.add_edge("analyst", END)

app = workflow.compile()