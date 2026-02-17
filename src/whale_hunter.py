import os
import random
import time
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
# 📉 MICRO-CAP SETTINGS
MAX_MARKET_CAP = 300_000_000   # Max: $300 Million
MIN_MARKET_CAP = 10_000_000    # Min: $10 Million (Avoid shells)
MAX_RETRIES = 1                # 2 Retries = 3 Total Attempts per region

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

# --- 3. THE WORKERS ---

def scout_node(state):
    """
    🕵️‍♂️ THE SCOUT: Finds a ticker.
    """
    region = state.get('region', 'USA')
    retries = state.get('retry_count', 0)
    
    print(f"\n🔭 [Attempt {retries+1}/{MAX_RETRIES+1}] Scouting {region} Micro-Caps...")
    
    # Randomize query to find fresh targets
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

    # LLM Extraction
    extraction_prompt = f"""
    ROLE: Financial Data Extractor.
    INPUT: {search_results}
    
    TASK: Extract the single most interesting MICRO-CAP stock ticker.
    CONSTRAINT: Must be listed in {region}. Ignore companies larger than $300M.
    
    OUTPUT: Just the ticker symbol (e.g., LMFA, ABF.L). No text.
    """
    
    try:
        if llm:
            ticker = llm.invoke(extraction_prompt).content.strip().upper()
            ticker = ticker.replace("$", "").replace("Ticker:", "").strip()
            # Clean junk
            if len(ticker) > 8 or " " in ticker: ticker = "NONE"
            
            print(f"   🎯 Target: {ticker}")
            return {"ticker": ticker}
        else:
            return {"ticker": "NONE"}
    except:
        return {"ticker": "NONE"}

def gatekeeper_node(state):
    """
    🛡️ THE GATEKEEPER: Filters by size and manages the Retry Counter.
    """
    ticker = state['ticker']
    current_retries = state.get('retry_count', 0)
    
    # 1. Check for Invalid Ticker
    if ticker == "NONE":
        print(f"   🚫 No valid ticker found. Incrementing Retry.")
        return {
            "is_small_cap": False, 
            "market_cap": 0, 
            "retry_count": current_retries + 1 # 👈 CRITICAL FIX
        }

    # 2. Check Market Cap
    print(f"   ⚖️ Weighing {ticker}...")
    try:
        stock = yf.Ticker(ticker)
        mkt_cap = stock.info.get('marketCap', 0)
        name = stock.info.get('shortName', ticker)
        
        if MIN_MARKET_CAP < mkt_cap < MAX_MARKET_CAP:
            print(f"   ✅ {ticker} Accepted (${mkt_cap:,.0f}).")
            return {"market_cap": mkt_cap, "is_small_cap": True, "company_name": name}
        else:
            print(f"   🚫 {ticker} Rejected (${mkt_cap:,.0f}). Incrementing Retry.")
            return {
                "market_cap": mkt_cap, 
                "is_small_cap": False, 
                "retry_count": current_retries + 1 # 👈 CRITICAL FIX
            }

    except Exception as e:
        print(f"   ❌ YFinance Error: {e}")
        return {
            "is_small_cap": False, 
            "market_cap": 0, 
            "retry_count": current_retries + 1 # 👈 CRITICAL FIX
        }

def analyst_node(state):
    """
    🧠 THE ANALYST: Runs Graham Logic.
    """
    ticker = state['ticker']
    print(f"   🧮 Analyzing {ticker}...")
    
    fin_data = check_financial_health(ticker)
    news = brave_market_search(f"{ticker} stock analysis")
    
    prompt = f"""
    Analyze {state.get('company_name', ticker)} ({ticker}).
    Market Cap: ${state.get('market_cap', 0):,.0f}
    Financials: {fin_data.get('metrics')}
    News: {news}
    Verdict: BUY / WATCH / AVOID.
    Thesis: 3 sentences max.
    """
    
    if llm:
        response = llm.invoke([SystemMessage(content="You are Benjamin Graham."), HumanMessage(content=prompt)])
        verdict = response.content
    else:
        verdict = f"Data: {fin_data.get('reason')}"
        
    return {"financial_data": fin_data, "final_verdict": verdict}

def email_node(state):
    """
    📧 THE REPORTER: Sends Success OR Failure reports.
    """
    region = state.get('region', 'Global')
    ticker = state.get('ticker', 'Unknown')
    verdict = state.get('final_verdict', 'No Verdict')
    
    # 🚨 FAILURE REPORT (New Feature)
    if not state.get('is_small_cap'):
        print(f"   ⚠️ Sending Failure Report for {region}...")
        subject = f"🧬 Micro-Cap Hunter: No Targets Found ({region})"
        html_body = f"""
        <h1>❌ Hunt Failed: {region}</h1>
        <p>Scouted {MAX_RETRIES + 1} times but found no companies meeting the strict Micro-Cap criteria ($10M - $300M).</p>
        <hr>
        <small>Agent: PrimoGreedy</small>
        """
    else:
        # ✅ SUCCESS REPORT
        print(f"   📨 Sending Analysis for {ticker}...")
        subject = f"🧬 Micro-Cap Found ({region}): {ticker}"
        html_body = f"""
        <h1>📍 Region: {region}</h1>
        <h2>Ticker: {ticker}</h2>
        <h3>Market Cap: ${state.get('market_cap', 0):,.0f}</h3>
        <hr>
        {verdict.replace(chr(10), '<br>')}
        <hr>
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
    # 1. Found a Gem? -> Analyze
    if state.get('is_small_cap'): 
        return "analyst"
    
    # 2. Retries exhausted? -> Email Failure
    if state.get('retry_count', 0) > MAX_RETRIES:
        return "email" # 👈 NOW GOES TO EMAIL INSTEAD OF END
    
    # 3. Try again? -> Loop back
    print("   🔄 Looping back...")
    time.sleep(2) # Safety Pause
    return "scout"

workflow.add_edge("scout", "gatekeeper")
workflow.add_conditional_edges("gatekeeper", check_status, 
    {"analyst": "analyst", "scout": "scout", "email": "email"}
)
workflow.add_edge("analyst", "email")
workflow.add_edge("email", END)

app = workflow.compile()

# 🟢 EXECUTION BLOCK
if __name__ == "__main__":
    print("🚀 Starting Micro-Cap Hunter (Senior Fixed Version)...")
    regions = ["USA", "UK"]
    
    for market in regions:
        print(f"\n--- 🏁 Hunt: {market} ---")
        try:
            # Explicitly reset retry_count to 0
            app.invoke({"region": market, "retry_count": 0, "ticker": ""})
            print(f"✅ {market} Complete.")
        except Exception as e:
            print(f"❌ Critical Error in {market}: {e}")

