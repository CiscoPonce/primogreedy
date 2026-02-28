import os
import re
import random
import time
import gc
import json
import requests
import yfinance as yf
from datetime import datetime, timezone, timedelta
from typing import TypedDict
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage

from src.llm import get_llm
from src.finance_tools import check_financial_health, get_insider_sentiment, get_company_news, get_basic_financials
from src.portfolio_tracker import record_paper_trade
import io
import matplotlib.pyplot as plt

def generate_chart(ticker: str) -> bytes:
    """Generates a 6-month price chart in memory."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="6mo")
        if hist.empty: return None
        plt.figure(figsize=(6, 4))
        plt.plot(hist.index, hist['Close'], color='#00a1ff', linewidth=1.5)
        plt.title(f"{ticker} - 6 Month Price Action")
        plt.grid(True, linestyle='--', alpha=0.6)
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close('all')
        return buf.getvalue()
    except:
        return None

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
    """Loads memory and applies the 30-Day Amnesia Protocol."""
    if not os.path.exists(MEMORY_FILE): return {}
    try:
        with open(MEMORY_FILE, "r") as f:
            mem = json.load(f)
            
        # 🚨 UPGRADE 1: THE AMNESIA PROTOCOL
        now = datetime.now(timezone.utc)
        cleaned_mem = {}
        for ticker, timestamp in mem.items():
            try:
                past = datetime.fromisoformat(timestamp)
                if (now - past) < timedelta(days=30):
                    cleaned_mem[ticker] = timestamp
            except: pass
        
        # Save the cleaned memory back to the file
        with open(MEMORY_FILE, "w") as f: json.dump(cleaned_mem, f, indent=4)
        return cleaned_mem
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

REGION_SUFFIXES = {"USA": [""], "UK": [".L"], "Canada": [".TO", ".V"], "Australia": [".AX"]}

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
    manual_search: bool  

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
def chat_node(state):
    """🚨 UPGRADE 3: Dedicated UI Chat Node."""
    user_query = state.get('ticker', '')
    prompt = f"""
    You are the Senior Broker AI for PrimoGreedy. Your team member just asked you this question:
    "{user_query}"
    
    Answer them directly, professionally, and concisely. If they are asking about financial metrics or how you work, explain your quantitative Graham and Deep Value frameworks.
    """
    response = llm.invoke(prompt).content if llm else "I am offline right now."
    return {"final_report": response, "status": "CHAT"}

def scout_node(state):
    region = state.get('region', 'USA')
    retries = state.get('retry_count', 0)
    
    is_manual = bool(state.get('ticker') and state['ticker'] != "NONE" and retries == 0)
    if is_manual:
        return {"ticker": resolve_ticker_suffix(state['ticker'], region), "manual_search": True}

    if retries > 0: time.sleep(2)
    
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
        
        lean_info = {
            'currentPrice': raw_info.get('currentPrice', 0) or raw_info.get('regularMarketPrice', 0),
            'trailingEps': raw_info.get('trailingEps', 0),
            'bookValue': raw_info.get('bookValue', 0),
            'marketCap': raw_info.get('marketCap', 0),
            'ebitda': raw_info.get('ebitda', 0),
            'sector': raw_info.get('sector', 'Unknown'),
            'freeCashflow': raw_info.get('freeCashflow', 0),
            'totalCash': raw_info.get('totalCash', 0)
        }
        del raw_info 
        gc.collect() 

        price = lean_info['currentPrice']
        mkt_cap = lean_info['marketCap']
        fcf = lean_info['freeCashflow']
        cash = lean_info['totalCash']
        
        # 🚨 FIX: Generate the chart before returning so it is always included
        chart_bytes = generate_chart(ticker)
        
        # UI Safety Soft Reject
        if price > MAX_PRICE_PER_SHARE:
            return {"market_cap": mkt_cap, "is_small_cap": False, "status": "FAIL", "company_name": stock.info.get('shortName', ticker), "financial_data": lean_info, "retry_count": retries + 1, "final_report": f"Price ${price} exceeds ${MAX_PRICE_PER_SHARE} limit.", "chart_data": chart_bytes}
        
        if not (MIN_MARKET_CAP < mkt_cap < MAX_MARKET_CAP):
            return {"market_cap": mkt_cap, "is_small_cap": False, "status": "FAIL", "company_name": stock.info.get('shortName', ticker), "financial_data": lean_info, "retry_count": retries + 1, "final_report": f"Market Cap ${mkt_cap:,.0f} is outside the $10M-$300M range.", "chart_data": chart_bytes}

        # 🚨 UPGRADE 2: SECTOR-SPECIFIC HEALTH CHECK
        health = check_financial_health(ticker, lean_info)
        if health["status"] == "FAIL":
            return {"market_cap": mkt_cap, "is_small_cap": False, "status": "FAIL", "company_name": stock.info.get('shortName', ticker), "financial_data": lean_info, "retry_count": retries + 1, "final_report": f"⚠️ **GATEKEEPER REJECT:** {health['reason']}", "chart_data": chart_bytes}

        # Passing state includes the chart
        return {"market_cap": mkt_cap, "is_small_cap": True, "status": "PASS", "company_name": stock.info.get('shortName', ticker), "financial_data": lean_info, "chart_data": chart_bytes}
    except Exception as e:
        return {"is_small_cap": False, "status": "FAIL", "retry_count": retries + 1, "financial_data": {"reason": f"API Error: {str(e)}"}}

def analyst_node(state):
    """
    🧠 THE SENIOR BROKER (ReAct Agent Upgrade)
    Now equipped with Finnhub tools for deep fundamental research.
    """
    ticker = state['ticker']
    info = state.get('financial_data', {})
    chart_bytes = state.get('chart_data') # Retrieve chart from Gatekeeper
    
    if state.get('status') == "FAIL":
        reason = state.get('final_report', info.get('reason', 'Failed basic criteria.'))
        verdict = f"### ❌ REJECTED BY GATEKEEPER\n**Reason:** {reason}\n\n*The data for {ticker} was retrieved, but it does not fit the PrimoGreedy small-cap profile.*"
        return {"final_verdict": verdict, "final_report": verdict, "chart_data": chart_bytes}
    
    price = info.get('currentPrice', 0)
    eps = info.get('trailingEps', 0)
    book_value = info.get('bookValue', 0)
    ebitda = info.get('ebitda', 0)
    sector = info.get('sector', 'Unknown')
    
    if eps > 0 and book_value > 0:
        strategy = "GRAHAM VALUE"
        valuation = (22.5 * eps * book_value) ** 0.5
        thesis = f"Profitable in {sector}. Graham Value ${valuation:.2f} vs Price ${price:.2f}. EBITDA: ${ebitda:,.0f}."
    else:
        strategy = "DEEP VALUE ASSET PLAY"
        valuation = book_value
        thesis = f"Unprofitable in {sector}. Trading at {(price/book_value if book_value > 0 else 0):.2f}x Book Value. EBITDA: ${ebitda:,.0f}. Assets are the safety net."

    news = brave_market_search(f"{ticker} stock {sector} catalysts insider buying")
    
    region = state.get('region', 'USA')
    
    prompt = f"""
    Act as a Senior Financial Broker evaluating {state.get('company_name')} ({ticker}).
    
    HARD DATA: Price: ${price} | EPS: {eps} | Book/Share: {book_value} | EBITDA: {ebitda}
    QUANTITATIVE THESIS: {thesis}
    """
    
    # 🟢 NEW: Agentic Tool Calling Loop for USA Stocks
    if llm and region == 'USA' and "." not in ticker:
        print(f"   🤖 Agent '{ticker}' is researching Finnhub databases...")
        tools = [get_insider_sentiment, get_company_news, get_basic_financials]
        llm_with_tools = llm.bind_tools(tools)
        
        # We manually invoke the tools here for the specific ticker to gather context before writing the memo
        context = ""
        try:
            context += get_insider_sentiment.invoke({"ticker": ticker}) + "\n"
            context += get_company_news.invoke({"ticker": ticker}) + "\n"
            context += get_basic_financials.invoke({"ticker": ticker}) + "\n"
            prompt += f"\nDEEP FUNDAMENTALS (FINNHUB):\n{context}\n"
        except Exception as e:
            print(f"   ⚠️ Tool error: {e}")
            prompt += f"\nNEWS (Fallback): {news}\n"
    else:
        prompt += f"\nNEWS: {str(news)[:1500]}\n"
    
    prompt += f"""
    Your task is to write a highly structured investment memo combining strict {strategy} math with qualitative analysis and recent insider behavior/news. Do not use fluff or buzzwords.
    
    Format your response EXACTLY like this:
    
    ### 🧮 THE QUANTITATIVE BASE (Graham / Asset Play)
    * State the current Price vs the calculated {strategy} valuation.
    * Briefly explain if the math supports a margin of safety.
    
    ### 🟢 THE LYNCH PITCH (Why I would own this)
    * **The Core Action:** In one sentence, what are insiders doing (buying/selling/neutral)? 
    * **The Catalyst:** Based on the news, what is the ONE simple reason this stock could run?
    
    ### 🔴 THE MUNGER INVERT (How I could lose money)
    * **Structural Weakness:** What is the most likely way an investor loses money here based on fundamentals/news?
    * **The Bear Evidence:** What exact metric, news, or math would prove the bear case right?
    
    ### ⚖️ FINAL VERDICT
    STRONG BUY / BUY / WATCH / AVOID (Choose one, followed by a 1-sentence bottom line).
    """
    verdict = llm.invoke(prompt).content if llm else f"Strategy: {strategy}"
    record_paper_trade(ticker, price, verdict, source="Chainlit UI")
    
    # Ensure chart data is passed along in the final response
    return {"final_verdict": verdict, "final_report": verdict, "chart_data": chart_bytes}

# --- GRAPH BUILDER ---
workflow = StateGraph(AgentState)
workflow.add_node("chat", chat_node)
workflow.add_node("scout", scout_node)
workflow.add_node("gatekeeper", gatekeeper_node)
workflow.add_node("analyst", analyst_node)

def initial_routing(state):
    """🚨 UPGRADE 3: Direct traffic at the very beginning."""
    query = str(state.get('ticker', ''))
    # 🚨 FIX: If the user typed a space, it goes to the chat node instantly.
    if " " in query: return "chat"
    return "scout"

workflow.set_conditional_entry_point(initial_routing, {"chat": "chat", "scout": "scout"})

def check_status(state):
    if state.get('manual_search'): return "analyst"
    if state.get('status') == "PASS": return "analyst"
    if state.get('retry_count', 0) >= MAX_RETRIES: return "analyst"
    return "scout"

workflow.add_edge("chat", END)
workflow.add_edge("scout", "gatekeeper")
workflow.add_conditional_edges("gatekeeper", check_status, {"analyst": "analyst", "scout": "scout"})
workflow.add_edge("analyst", END)

app = workflow.compile()