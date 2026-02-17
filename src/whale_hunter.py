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

# --- 1. CONFIGURATION (STRICT MODE) ---
# 📉 We are hunting MICRO-CAPS now.
MAX_MARKET_CAP = 300_000_000   # Limit: $300 Million (Strict)
MIN_MARKET_CAP = 20_000_000    # Min: $20 Million (Avoid total garbage)
MAX_RETRIES = 3                # Try harder to find a match

# --- 2. THE MEMORY (State) ---
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

# --- 3. THE WORKERS (Nodes) ---

def scout_node(state):
    """
    🕵️‍♂️ THE MICRO-CAP SCOUT
    Searches specifically for 'Microcap' and 'Nano Cap' opportunities.
    """
    region = state.get('region', 'USA')
    retries = state.get('retry_count', 0)
    
    print(f"🔭 Scouting {region} Micro-Caps... (Attempt {retries + 1})")
    
    # 🟢 NEW QUERIES: Explicitly ask for "Microcap" to avoid $1B companies
    base_queries = [
        f"undervalued microcap stocks {region} under $300m market cap",
        f"profitable nano cap stocks {region} 2026",
        f"hidden gem microcap stocks {region} with low float",
        f"debt free microcap companies {region} high growth",
        f"insider buying microcap stocks {region} this week"
    ]
    
    query = random.choice(base_queries)
    print(f"   ↳ Query: '{query}'")

    search_results = brave_market_search(query)
    
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
            if len(ticker) > 8 or " " in ticker: ticker = "NONE"
            
            print(f"   🎯 Target Acquired: {ticker}")
            return {"ticker": ticker, "retry_count": retries}
        else:
            return {"ticker": "NONE", "retry_count": retries}
            
    except Exception as e:
        print(f"   ❌ Extraction Error: {e}")
        return {"ticker": "NONE", "retry_count": retries}

def gatekeeper_node(state):
    """
    🛡️ THE STRICT GATEKEEPER
    Rejects anything over $300M.
    """
    ticker = state['ticker']
    if ticker == "NONE": return {"is_small_cap": False, "market_cap": 0}

    print(f"⚖️ Weighing {ticker}...")
    try:
        stock = yf.Ticker(ticker)
        
        # Get Market Cap
        mkt_cap = stock.info.get('marketCap', 0)
        name = stock.info.get('shortName', ticker)
        
        # 🟢 STRICT LOGIC: $20M - $300M Range
        if MIN_MARKET_CAP < mkt_cap < MAX_MARKET_CAP:
            print(f"✅ {ticker} is a Micro-Cap (${mkt_cap:,.0f}). Accepted.")
            return {"market_cap": mkt_cap, "is_small_cap": True, "company_name": name}
        else:
            print(f"🚫 {ticker} Rejected (${mkt_cap:,.0f}). Too Big/Small. Retry.")
            return {"market_cap": mkt_cap, "is_small_cap": False, "company_name": name}

    except:
        return {"is_small_cap": False, "market_cap": 0}

def analyst_node(state):
    """
    🧠 THE ANALYST (Graham Logic)
    """
    ticker = state['ticker']
    fin_data = check_financial_health(ticker)
    news = brave_market_search(f"{ticker} stock investor analysis")
    
    prompt = f"""
    Analyze {state['company_name']} ({ticker}) for a Value Investor.
    Market Cap: ${state.get('market_cap', 0):,.0f} (Micro-Cap)
    
    GRAHAM DATA:
    {fin_data.get('metrics')}
    Health Check: {fin_data.get('reason')}
    
    MARKET NEWS:
    {news}
    
    TASK:
    Write a concise thesis.
    Does it pass the Graham Number test?
    
    VERDICT: BUY / WATCH / AVOID.
    """
    
    if llm:
        response = llm.invoke([SystemMessage(content="You are Benjamin Graham."), HumanMessage(content=prompt)])
        verdict = response.content
    else:
        verdict = f"Data: {fin_data.get('reason')}"
        
    return {"financial_data": fin_data, "final_verdict": verdict}

def email_node(state):
    """
    📧 THE REPORTER
    """
    ticker = state.get('ticker', 'Unknown')
    region = state.get('region', 'Global')
    verdict = state.get('final_verdict', 'No Verdict')
    
    if not state.get('is_small_cap'):
        print(f"⚠️ No valid Micro-Cap found for {region} after retries.")
        return {}
    
    subject = f"🧬 Micro-Cap Hunter ({region}): {ticker}"
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
    if state['is_small_cap']: return "analyst"
    if state['retry_count'] < MAX_RETRIES:
        state['retry_count'] += 1
        return "scout"
    return END

workflow.add_edge("scout", "gatekeeper")
workflow.add_conditional_edges("gatekeeper", check_status, {"analyst": "analyst", "scout": "scout", END: END})
workflow.add_edge("analyst", "email")
workflow.add_edge("email", END)

app = workflow.compile()

# 🟢 EXECUTION BLOCK
if __name__ == "__main__":
    print("🚀 Starting Micro-Cap Hunter (Sprint 7)...")
    regions = ["USA", "UK", "Canada", "Australia"]
    
    for market in regions:
        print(f"\n--- 🏁 Initiating Hunt for {market} ---")
        try:
            app.invoke({"region": market, "retry_count": 0, "ticker": ""})
            print(f"✅ {market} Hunt Complete.")
            time.sleep(2)
        except Exception as e:
            print(f"❌ Error in {market}: {e}")
            
    print("\n🎉 Global Mission Complete.")