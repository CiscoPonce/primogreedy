import os
import random
from typing import TypedDict, Annotated, List, Union
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage

# Import tools
import yfinance as yf
from src.llm import get_llm 
from src.finance_tools import check_financial_health
from src.email_utils import send_email_report
from src.agent import brave_market_search 

# --- 1. CONFIGURATION ---
MAX_MARKET_CAP = 500_000_000   # < $500M
MIN_MARKET_CAP = 1_000_000     # > $1M (Avoid Penny Stocks/Ghosts)
MAX_RETRIES = 3                # Try 3 different stocks before giving up

# --- 2. THE MEMORY (State) ---
class AgentState(TypedDict):
    ticker: str             
    company_name: str       
    market_cap: float       
    is_small_cap: bool      
    financial_data: dict    
    final_verdict: str      
    retry_count: int        # 🔄 Track how many times we tried

llm = get_llm()

# --- 3. THE WORKERS (Nodes) ---

def scout_node(state):
    """
    🕵️‍♂️ THE SCOUT
    Searches for a new target. 
    If this is a retry, it changes the search query to find something fresh.
    """
    retries = state.get('retry_count', 0)
    print(f"🔭 Scouting... (Attempt {retries + 1}/{MAX_RETRIES + 1})")
    
    # 1. diverse queries to ensure we don't find the same stock twice
    queries = [
        "undervalued small cap stocks USA today",
        "top rated microcap stocks 2026",
        "insider buying small cap stocks this week",
        "deep value small cap stocks UK",
        "turnaround stocks under $500 million market cap",
        "net net stocks list 2026"
    ]
    
    # Pick a random query
    query = random.choice(queries)
    print(f"   ↳ Query: '{query}'")

    # 2. Search
    search_results = brave_market_search(query)
    
    # 3. LLM Extraction
    extraction_prompt = f"""
    ROLE: Financial Data Extractor.
    INPUT: {search_results}
    
    TASK: Extract the single most interesting stock ticker.
    CONSTRAINT: Do NOT pick '{state.get('ticker', 'None')}'. Pick a DIFFERENT one if possible.
    
    OUTPUT: Just the ticker symbol (e.g., LMFA). No text.
    """
    
    try:
        if llm:
            ticker = llm.invoke(extraction_prompt).content.strip().upper()
            ticker = ticker.replace("$", "").replace("Ticker:", "").strip()
            # Remove junk length
            if len(ticker) > 6 or " " in ticker: ticker = "LMFA"
            
            print(f"   🎯 Target Acquired: {ticker}")
            return {"ticker": ticker, "retry_count": retries}
        else:
            return {"ticker": "LMFA", "retry_count": retries}
            
    except Exception as e:
        print(f"   ❌ Extraction Error: {e}")
        return {"ticker": "LMFA", "retry_count": retries}

def gatekeeper_node(state):
    """
    🛡️ THE STRICT GATEKEEPER
    Now rejects $0 Market Caps and forces a Retry.
    """
    ticker = state['ticker']
    print(f"⚖️ Weighing {ticker}...")
    
    try:
        stock = yf.Ticker(ticker)
        mkt_cap = stock.info.get('marketCap', 0)
        name = stock.info.get('shortName', ticker)
        
        # 🟢 STRICT LOGIC: Must be between $1M and $500M
        if MIN_MARKET_CAP < mkt_cap < MAX_MARKET_CAP:
            print(f"✅ {ticker} is a Valid Gem (${mkt_cap:,.0f}). Proceeding.")
            return {"market_cap": mkt_cap, "is_small_cap": True, "company_name": name}
            
        else:
            print(f"🚫 {ticker} Rejected. (Cap: ${mkt_cap:,.0f}). Requesting Retry.")
            # We return False so the graph loops back
            return {"market_cap": mkt_cap, "is_small_cap": False, "company_name": name}

    except Exception as e:
        print(f"❌ Gatekeeper Error: {e}")
        return {"is_small_cap": False, "market_cap": 0}

def analyst_node(state):
    """
    🧠 THE ANALYST
    """
    ticker = state['ticker']
    print(f"🧮 Analyzing {ticker}...")
    
    fin_data = check_financial_health(ticker)
    news = brave_market_search(f"{ticker} stock analysis")
    
    prompt = f"""
    Analyze {state['company_name']} ({ticker}).
    Market Cap: ${state.get('market_cap', 'N/A')}
    Financials: {fin_data.get('reason')}
    Metrics: {fin_data.get('metrics')}
    News: {news}
    
    Verdict: BUY / WATCH / AVOID.
    Thesis: 3 sentences max.
    """
    
    if llm:
        response = llm.invoke([SystemMessage(content="You are a value investor."), HumanMessage(content=prompt)])
        verdict = response.content
    else:
        verdict = f"Data: {fin_data.get('reason')}"
        
    return {"financial_data": fin_data, "final_verdict": verdict}

def email_node(state):
    """
    📧 THE REPORTER
    """
    ticker = state.get('ticker', 'Unknown')
    verdict = state.get('final_verdict', 'No Verdict')
    
    # If we failed after 3 tries, send a failure report so we know.
    if not state.get('is_small_cap'):
        subject = "⚠️ Whale Hunter: Search Failed (3 Attempts)"
        html_body = f"<h1>Search Failed</h1><p>Tried 3 times. Last attempt: {ticker} (Cap: ${state.get('market_cap')})</p>"
    else:
        subject = f"🐳 Whale Hunter: {ticker} Analysis"
        html_body = f"""
        <h1>🌊 Whale Hunter Report: {ticker}</h1>
        <h3>Market Cap: ${state.get('market_cap', 0):,.0f}</h3>
        <hr>
        <p>{verdict.replace(chr(10), '<br>')}</p>
        <hr>
        <small>Generated by LangGraph Agent</small>
        """
    
    print(f"📨 Sending Email: {subject}")

    team = [
        {"name": "Cisco", "email": os.getenv("EMAIL_CISCO"), "key": os.getenv("RESEND_API_KEY_CISCO")},
        {"name": "Raul",  "email": os.getenv("EMAIL_RAUL"),  "key": os.getenv("RESEND_API_KEY_RAUL")},
        {"name": "David", "email": os.getenv("EMAIL_DAVID"), "key": os.getenv("RESEND_API_KEY_DAVID")}
    ]
    
    for member in team:
        if member["email"] and member["key"]:
            try:
                send_email_report(subject, html_body, member["email"], member["key"])
            except: pass
            
    return {}

# --- 4. THE GRAPH (Manager) ---

workflow = StateGraph(AgentState)

workflow.add_node("scout", scout_node)
workflow.add_node("gatekeeper", gatekeeper_node)
workflow.add_node("analyst", analyst_node)
workflow.add_node("email", email_node)

workflow.set_entry_point("scout")

# 🟢 THE LOOP LOGIC
def check_status(state):
    if state['is_small_cap']:
        return "analyst"  # ✅ Found one! Analyze it.
    
    if state['retry_count'] < MAX_RETRIES:
        # 🔄 Increment retry and LOOP BACK to scout
        state['retry_count'] += 1
        return "scout"
    
    return "email" # ❌ Give up and email failure report

workflow.add_edge("scout", "gatekeeper")

workflow.add_conditional_edges(
    "gatekeeper",
    check_status,
    {
        "analyst": "analyst",
        "scout": "scout",    # 👈 The Loop
        "email": "email"
    }
)

workflow.add_edge("analyst", "email")
workflow.add_edge("email", END)

app = workflow.compile()

# 🟢 EXECUTION BLOCK
if __name__ == "__main__":
    print("🚀 Starting Whale Hunter Agent (Sprint 7)...")
    try:
        # Initialize retry_count to 0
        result = app.invoke({"ticker": "", "retry_count": 0})
        print("✅ Mission Complete.")
    except Exception as e:
        print(f"❌ CRITICAL FAILURE: {str(e)}")