import os
from typing import TypedDict, Optional, Any
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage
import requests
import yfinance as yf
from src.llm import get_llm
from src.finance_tools import check_financial_health
from src.viz import get_stock_chart
from src.email_utils import send_email_report

# --- 1. STATE DEFINITION ---
class AgentState(TypedDict):
    ticker: str
    status: str
    financial_data: Optional[dict]
    final_report: Optional[str]
    chart_data: Optional[Any]
    email_status: Optional[str]
    insider_info: Optional[str]

llm = get_llm()

# --- 2. TOOLS ---

def brave_market_search(query: str):
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key: return "⚠️ Brave Key Missing."
    
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": api_key}
    params = {"q": f"{query} stock news analysis moat competition", "count": 5, "freshness": "pw"}
    
    try:
        data = requests.get(url, headers=headers, params=params).json()
        results = data.get("web", {}).get("results", [])
        snippets = [f"HEADLINE: {r['title']}\nSNIPPET: {r['description']}" for r in results]
        return "\n\n".join(snippets)
    except Exception as e:
        return f"Search Error: {str(e)}"

def get_insider_activity(ticker: str):
    try:
        stock = yf.Ticker(ticker)
        insiders = stock.insider_transactions
        if insiders is None or insiders.empty:
            return "No recent insider data."
        return f"Recent Insider Activity:\n{insiders.head(3).to_string()}"
    except:
        return "Could not retrieve insider data."

# --- 3. NODES ---

def check_health(state: AgentState):
    """The Graham Logic Firewall Node."""
    ticker = state['ticker'].upper()
    result = check_financial_health(ticker)
    return {"financial_data": result, "status": result['status']}

async def analyze_stock(state: AgentState):
    """The Buffett Analyst Node."""
    if state['status'] == "FAIL": return state

    ticker = state['ticker'].upper()
    
    try:
        stock = yf.Ticker(ticker)
        company_name = stock.info.get('shortName') or stock.info.get('longName') or ticker
    except:
        company_name = ticker

    # Search for Moat & Competition
    search_query = f"{company_name} competitive advantage moat analysis"
    market_news = brave_market_search(search_query)
    
    chart_bytes = get_stock_chart(ticker)
    insider_data = get_insider_activity(ticker)
    
    # Extract the Graham Metrics calculated in finance_tools.py
    metrics = state['financial_data'].get('metrics', {})
    graham_val = metrics.get('intrinsic_value', 'N/A')
    safety_margin = metrics.get('margin_of_safety', 'N/A')
    sector = metrics.get('sector', 'Unknown')

    # --- THE BUFFETT PROMPT ---
    prompt = f"""
    ROLE: You are PrimoGreedy, a Value Investor following the philosophy of Benjamin Graham and Warren Buffett.
    
    OBJECTIVE: Determine if {company_name} ({ticker}) is a "Wonderful Company at a Fair Price."

    HARD DATA INPUTS:
    - Sector: {sector}
    - Current Price: ${metrics.get('current_price')}
    - Graham Intrinsic Value: ${graham_val}
    - Margin of Safety (Calculated): {safety_margin}% (Target > 15%)
    - Debt/Equity: {metrics.get('debt_to_equity')}
    
    QUALITATIVE INPUTS (News & Search):
    {market_news}
    
    INSIDER TRADING:
    {insider_data}

    ANALYSIS FRAMEWORK (Follow Strict Logic):
    
    1. BUSINESS QUALITY (THE MOAT)
       - Does it have a sustainable competitive advantage? (Brand, Switching Costs, Network Effect).
       - Is it a "Wonderful Company"?

    2. FINANCIAL STRENGTH (THE SHIELD)
       - The Code already passed the Solvency Check, but qualitatively: is the balance sheet "Anti-Fragile"?
       
    3. VALUATION (THE PRICE)
       - "Price is what you pay, Value is what you get."
       - Use the Graham Intrinsic Value ($ {graham_val}) as a baseline.
       - Is it trading at a discount?

    FINAL VERDICT:
    - BUY: High Quality (Moat) + Discount (Margin of Safety).
    - HOLD: High Quality but Expensive.
    - AVOID: Low Quality OR Dangerous Fundamentals.
    
    OUTPUT FORMAT:
    Start with "VERDICT: [BUY/HOLD/AVOID]".
    Then write a 3-5 line "Thesis" explaining WHY.
    Finally, list one "Key Risk" (e.g., Competition, Regulation).
    """
    
    response = await llm.ainvoke([
        SystemMessage(content="You are a disciplined Value Investor. You are skeptical of hype."), 
        HumanMessage(content=prompt)
    ])
    
    email_result = send_email_report(ticker, response.content)
    
    return {
        "final_report": response.content, 
        "chart_data": chart_bytes,
        "email_status": email_result
    }

async def chat_mode(state: AgentState):
    response = await llm.ainvoke([HumanMessage(content=state['ticker'])])
    return {"final_report": response.content, "status": "CHAT"}

# --- 4. GRAPH SETUP ---

def route_query(state: AgentState):
    query = state['ticker'].strip().upper()
    if 1 <= len(query) <= 5 and " " not in query:
        return "financial_health_check"
    return "chat_mode"

workflow = StateGraph(AgentState)
workflow.add_node("financial_health_check", check_health)
workflow.add_node("analyst_research", analyze_stock)
workflow.add_node("chat_mode", chat_mode)

workflow.set_conditional_entry_point(route_query, {"financial_health_check": "financial_health_check", "chat_mode": "chat_mode"})
workflow.add_edge("financial_health_check", "analyst_research")
workflow.add_edge("analyst_research", END)
workflow.add_edge("chat_mode", END)

app = workflow.compile()