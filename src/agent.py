import re
import random
import time
import gc
import yfinance as yf
from typing import TypedDict
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage
from src.agent_tools import brave_market_search

# Import your tools (adjust these imports based on your exact file structure)
from src.llm import get_llm
from src.agent_tools import brave_market_search 

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
    status: str          # For app.py compatibility (PASS/FAIL)
    final_report: str    # For app.py compatibility
    chart_data: bytes    # For app.py compatibility

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
    # If a ticker was manually passed (e.g., from app.py), skip scouting
    if state.get('ticker') and state['ticker'] != "NONE" and retries == 0:
        return {"ticker": resolve_ticker_suffix(state['ticker'], region)}

    if retries > 0: time.sleep(2)
    
    base_queries = [
        f"undervalued microcap stocks {region} under $300m market cap",
        f"profitable nano cap stocks {region} 2026",
        f"hidden gem microcap stocks {region} with low float"
    ]
    try:
        search_results = brave_market_search(random.choice(base_queries))
        raw = llm.invoke(f"Extract single MICRO-CAP ticker in {region} from: {str(search_results)[:2000]}").content
        ticker = extract_ticker_from_text(raw)
        if ticker != "NONE": ticker = resolve_ticker_suffix(ticker, region)
        return {"ticker": ticker}
    except: return {"ticker": "NONE"}

def gatekeeper_node(state):
    ticker = state.get('ticker', 'NONE')
    retries = state.get('retry_count', 0)
    
    if ticker == "NONE":
        return {"is_small_cap": False, "status": "FAIL", "retry_count": retries + 1, "financial_data": {"reason": "No ticker found"}}

    try:
        stock = yf.Ticker(ticker)
        raw_info = stock.info
        
        # 🚨 THE DATA DIET: Extract only what we need, then delete raw_info to prevent Memory Crashes
        lean_info = {
            'currentPrice': raw_info.get('currentPrice', 0) or raw_info.get('regularMarketPrice', 0),
            'trailingEps': raw_info.get('trailingEps', 0),
            'bookValue': raw_info.get('bookValue', 0),
            'marketCap': raw_info.get('marketCap', 0),
            'ebitda': raw_info.get('ebitda', 0),
            'sector': raw_info.get('sector', 'Unknown')
        }
        del raw_info 
        gc.collect() # Force immediate RAM cleanup

        price = lean_info['currentPrice']
        mkt_cap = lean_info['marketCap']
        
        # Price & Cap Filters
        if price > MAX_PRICE_PER_SHARE:
            return {"is_small_cap": False, "status": "FAIL", "retry_count": retries + 1, "financial_data": {"reason": f"Price ${price} > ${MAX_PRICE_PER_SHARE}"}}
        if not (MIN_MARKET_CAP < mkt_cap < MAX_MARKET_CAP):
            return {"is_small_cap": False, "status": "FAIL", "retry_count": retries + 1, "financial_data": {"reason": f"Cap ${mkt_cap:,.0f} out of bounds"}}

        return {"market_cap": mkt_cap, "is_small_cap": True, "status": "PASS", "company_name": stock.info.get('shortName', ticker), "financial_data": lean_info}
    except Exception as e:
        return {"is_small_cap": False, "status": "FAIL", "retry_count": retries + 1, "financial_data": {"reason": f"API Error: {str(e)}"}}

def analyst_node(state):
    ticker = state['ticker']
    info = state.get('financial_data', {})
    
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
    STRATEGY: {strategy}
    DATA: Price: ${price} | EPS: {eps} | Book/Share: {book_value} | EBITDA: {ebitda}
    CONTEXT: {thesis}
    NEWS: {str(news)[:1500]}
    
    OUTPUT:
    VERDICT: STRONG BUY / BUY / WATCH / AVOID
    RATIONALE: Max 3 sentences weighing Graham valuation/Assets against News.
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
    if state.get('is_small_cap'): return "analyst"
    if state.get('retry_count', 0) > MAX_RETRIES: return END
    return "scout"

workflow.add_edge("scout", "gatekeeper")
workflow.add_conditional_edges("gatekeeper", check_status, {"analyst": "analyst", "scout": "scout", END: END})
workflow.add_edge("analyst", END)

app = workflow.compile()
