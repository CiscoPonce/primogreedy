import os
from typing import TypedDict, Optional, Any
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage
import requests
from src.llm import get_llm
from src.finance_tools import check_financial_health
from src.viz import get_stock_chart
from src.email_utils import send_email_report

# --- 1. STATE ---
class AgentState(TypedDict):
    ticker: str
    status: str
    financial_data: Optional[dict]
    final_report: Optional[str]
    chart_data: Optional[Any]
    email_status: Optional[str]

llm = get_llm()

# ... (Keep imports and State definition) ...

# --- 2. TOOLS ---
def brave_market_search(query: str):
    """
    Uses Brave API to find real-time news.
    Now supports more specific queries to avoid 'IT' or 'AI' confusion.
    """
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key: return "⚠️ Brave Key Missing."
    
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": api_key}
    
    # We search for "Ticker + Stock + News" to be specific
    params = {"q": f"{query} stock news analysis", "count": 5, "freshness": "pw"}
    
    try:
        data = requests.get(url, headers=headers, params=params).json()
        results = data.get("web", {}).get("results", [])
        
        # We grab the Title AND the Description to give the LLM more context
        snippets = [f"HEADLINE: {r['title']}\nSNIPPET: {r['description']}" for r in results]
        return "\n\n".join(snippets)
    except Exception as e:
        return f"Search Error: {str(e)}"

# ... (Keep get_stock_chart and get_insider_activity) ...

# --- 3. NODES ---

# ... (Keep check_health) ...

async def analyze_stock(state: AgentState):
    # Standard Efficiency Check
    if state['status'] == "FAIL": return state

    ticker = state['ticker'].upper()
    
    # ---------------------------------------------------
    # NEW: Get the Real Company Name for better Search
    # ---------------------------------------------------
    try:
        stock = yf.Ticker(ticker)
        company_name = stock.info.get('shortName') or stock.info.get('longName') or ticker
    except:
        company_name = ticker

    # Search for "Gartner Inc" instead of just "IT"
    search_query = f"{ticker} {company_name}"
    market_news = brave_market_search(search_query)
    
    # Run other tools
    chart_bytes = get_stock_chart(ticker)
    insider_data = get_insider_activity(ticker)
    
    prompt = f"""
    Analyze {company_name} ({ticker}).
    
    Financial Health: {state['financial_data']['reason']}
    
    Real-Time News Search:
    {market_news}
    
    Insider Trading Data:
    {insider_data}
    
    Task:
    1. ignore generic news not related to {company_name}.
    2. Synthesize the financial data with the news sentiment.
    3. Give a clear BUY, SELL, or HOLD recommendation.
    """
    
    response = await llm.ainvoke([
        SystemMessage(content="You are PrimoGreedy. Sarcastic, skeptical, data-driven."), 
        HumanMessage(content=prompt)
    ])
    
    # Send Email
    email_result = send_email_report(ticker, response.content)
    
    return {
        "final_report": response.content, 
        "chart_data": chart_bytes,
        "insider_info": insider_data,
        "email_status": email_result
    }

# --- 4. ROUTER ---
def route_query(state: AgentState):
    query = state['ticker'].strip().upper()
    if 1 <= len(query) <= 5 and " " not in query:
        return "financial_health_check"
    return "chat_mode"

# --- 5. GRAPH ---
workflow = StateGraph(AgentState)
workflow.add_node("financial_health_check", check_health)
workflow.add_node("analyst_research", analyze_stock)
workflow.add_node("chat_mode", chat_mode)

workflow.set_conditional_entry_point(route_query, {"financial_health_check": "financial_health_check", "chat_mode": "chat_mode"})
workflow.add_edge("financial_health_check", "analyst_research")
workflow.add_edge("analyst_research", END)
workflow.add_edge("chat_mode", END)

app = workflow.compile()