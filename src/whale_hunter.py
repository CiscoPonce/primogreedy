import os
import operator
from typing import TypedDict, Annotated, List, Union
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage

# Import our existing tools
import yfinance as yf
from src.llm import get_llm 
from src.finance_tools import check_financial_health
from src.email_utils import send_email_report
from src.agent import brave_market_search # Re-using the search tool from agent.py

# --- 1. CONFIGURATION ---
# 📉 "Small Cap" Definition: Companies under $500 Million.
# We ignore anything larger to focus on "Deep Value."
MAX_MARKET_CAP = 500_000_000 

# --- 2. THE MEMORY (State) ---
class AgentState(TypedDict):
    ticker: str             # The symbol (e.g., LMFA)
    company_name: str       # Official Name
    market_cap: float       # The size (e.g., $10M)
    is_small_cap: bool      # True/False Flag
    financial_data: dict    # Graham Numbers
    final_verdict: str      # Buy/Avoid text
    retry_count: int        # Loop Counter (to avoid infinite loops)

# Initialize LLM
llm = get_llm()

# --- 3. THE WORKERS (Nodes) ---

def scout_node(state):
    """
    🕵️‍♂️ THE REAL SCOUT (Live Mode)
    Searches for fresh small-cap opportunities and extracts the best ticker.
    """
    print(f"🔭 Scouting Global Markets...")

    # 1. Define the Hunt (We rotate regions or pick one)
    # You can randomize this or hardcode a specific region per run
    queries = [
        "undervalued small cap stocks USA today news",
        "top undervalued UK small cap stocks February 2026",
        "ASX small cap value stocks winners today",
        "TSX Venture undervalued mining stocks news"
    ]
    
    # Pick a random query or cycle them (for now, let's grab USA/Global)
    import random
    query = random.choice(queries)
    print(f"   ↳ Query: '{query}'")

    # 2. Perform Real Search
    search_results = brave_market_search(query)
    
    if not search_results or "Error" in search_results:
        print("   ⚠️ Search failed. Falling back to Safety.")
        return {"ticker": "LMFA"} # Only use fallback on strict failure

    # 3. Use LLM to Extract the Best Ticker
    # The search gives us text; we need the LLM to pick the specific symbol.
    extraction_prompt = f"""
    ROLE: You are a Financial Data Extractor.
    
    INPUT DATA (Search Results):
    {search_results}
    
    TASK:
    Identify the single most interesting "Small Cap" or "Undervalued" stock ticker mentioned in the text.
    Ignore large giants (like NVDA, TSLA).
    
    OUTPUT FORMAT:
    Return ONLY the ticker symbol (e.g., LMFA, ABF.L, ALK.AX). 
    Do not add markdown, explanation, or punctuation. just the symbol.
    """
    
    try:
        if llm:
            ticker = llm.invoke(extraction_prompt).content.strip().upper()
            # Clean up ticker (remove $ or extra spaces)
            ticker = ticker.replace("$", "").replace("Ticker:", "").strip()
            print(f"   🎯 Target Acquired: {ticker}")
            return {"ticker": ticker}
        else:
            return {"ticker": "LMFA"} # No LLM available
            
    except Exception as e:
        print(f"   ❌ Extraction Error: {e}")
        return {"ticker": "LMFA"}
    
def gatekeeper_node(state):
    """
    🛡️ THE FILTER
    Checks if the company is actually small enough (< $500M).
    """
    ticker = state['ticker']
    print(f"⚖️ Weighing {ticker}...")
    
    try:
        stock = yf.Ticker(ticker)
        # Fast fetch of market cap
        mkt_cap = stock.info.get('marketCap', 0)
        name = stock.info.get('shortName', ticker)
        
        # 🟢 THE LOGIC
        if 0 < mkt_cap < MAX_MARKET_CAP:
            print(f"✅ {ticker} is a Small Cap (${mkt_cap:,.0f}). Proceeding.")
            return {"market_cap": mkt_cap, "is_small_cap": True, "company_name": name}
            
        elif mkt_cap >= MAX_MARKET_CAP:
            print(f"🚫 {ticker} is too big (${mkt_cap:,.0f}). Stopping.")
            return {"market_cap": mkt_cap, "is_small_cap": False, "company_name": name}
            
        else:
            print(f"⚠️ Could not verify Cap for {ticker}. Assuming Small.")
            return {"market_cap": 0, "is_small_cap": True, "company_name": name}

    except Exception as e:
        print(f"❌ Gatekeeper Error: {e}")
        return {"is_small_cap": False} # Fail safe

def analyst_node(state):
    """
    🧠 THE ANALYST
    Runs the Graham Number logic and writes the specific thesis.
    """
    ticker = state['ticker']
    print(f"🧮 Analyzing {ticker}...")
    
    # 1. Run Math
    fin_data = check_financial_health(ticker)
    
    # 2. Run Qualitative Search
    news = brave_market_search(f"{ticker} stock news analysis")
    
    # 3. Ask LLM for Verdict
    prompt = f"""
    Analyze {state['company_name']} ({ticker}).
    Market Cap: ${state.get('market_cap', 'N/A')}
    
    Financial Health: {fin_data.get('reason')}
    Graham Data: {fin_data.get('metrics')}
    
    Recent News:
    {news}
    
    Task: Write a strict Value Investing Thesis.
    Focus on: Downside Protection (Margin of Safety) vs Upside Potential.
    Verdict: BUY / WATCH / AVOID.
    """
    
    if llm:
        response = llm.invoke([SystemMessage(content="You are a skeptical Value Investor."), HumanMessage(content=prompt)])
        verdict = response.content
    else:
        verdict = f"Simulated Verdict: {fin_data.get('reason')}"
        
    return {"financial_data": fin_data, "final_verdict": verdict}

def email_node(state):
    """
    📧 THE REPORTER (Robust Version)
    Sends an email even if the hunt failed, so we know the agent is alive.
    """
    ticker = state.get('ticker', 'Unknown')
    verdict = state.get('final_verdict', 'No Verdict Generated.')
    
    # 🚨 DEBUG: Print to logs
    print(f"📨 Email Node Triggered for {ticker}")
    print(f"   Verdict Length: {len(verdict)} chars")
        
    print(f"📨 Preparing email dispatch for {ticker}...")

    # 1. Define Team
    team = [
        {"name": "Cisco", "email": os.getenv("EMAIL_CISCO"), "key": os.getenv("RESEND_API_KEY_CISCO")},
        {"name": "Raul",  "email": os.getenv("EMAIL_RAUL"),  "key": os.getenv("RESEND_API_KEY_RAUL")},
        {"name": "David", "email": os.getenv("EMAIL_DAVID"), "key": os.getenv("RESEND_API_KEY_DAVID")}
    ]
    
    # 2. Format HTML
    subject = f"🐳 Whale Hunter: {ticker} Analysis"
    html_body = f"""
    <h1>🌊 Whale Hunter Report: {ticker}</h1>
    <h3>Market Cap: ${state.get('market_cap', 0):,.0f}</h3>
    <hr>
    <p>{verdict.replace(chr(10), '<br>')}</p>
    <hr>
    <small>Generated by LangGraph Agent (Sprint 7)</small>
    """
    
    # 3. Send Loop
    results = []
    for member in team:
        if member["email"] and member["key"]:
            try:
                send_email_report(subject, html_body, member["email"], member["key"])
                results.append(f"Sent to {member['name']}")
            except Exception as e:
                print(f"Failed to send to {member['name']}: {e}")
    
    return {"final_verdict": verdict} # Pass through

# --- 4. THE GRAPH (Manager) ---

workflow = StateGraph(AgentState)

# Add Nodes
workflow.add_node("scout", scout_node)
workflow.add_node("gatekeeper", gatekeeper_node)
workflow.add_node("analyst", analyst_node)
workflow.add_node("email", email_node)

# Set Entry Point
workflow.set_entry_point("scout")

# --- 5. THE ROUTING LOGIC ---
def check_size(state):
    if state['is_small_cap']:
        return "analyst"  # 🟢 Small enough? Analyze it.
    else:
        return END        # 🔴 Too big? Stop immediately.

workflow.add_edge("scout", "gatekeeper")

workflow.add_conditional_edges(
    "gatekeeper",
    check_size,
    {
        "analyst": "analyst",
        END: END
    }
)

workflow.add_edge("analyst", "email")
workflow.add_edge("email", END)

# Compile
app = workflow.compile()



# 🟢 THE EXECUTION BLOCK )
if __name__ == "__main__":
    print("🚀 Starting Whale Hunter Agent (Sprint 7)...")
    
    try:
        # Run the graph
        # We pass an empty ticker to trigger the 'Scout Node' logic
        result = app.invoke({"ticker": ""})
        
        print("✅ Mission Complete.")
        print(f"Final Verdict: {result.get('final_verdict')}")
        
    except Exception as e:
        print(f"❌ CRITICAL FAILURE: {str(e)}")

