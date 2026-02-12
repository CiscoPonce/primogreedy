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

# --- 2. TOOLS ---
def brave_market_search(query: str):
    """Uses Brave API to find real-time news."""
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key: return "⚠️ Brave Key Missing."
    
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": api_key}
    params = {"q": f"{query} stock news sentiment", "count": 3}
    
    try:
        data = requests.get(url, headers=headers, params=params).json()
        results = data.get("web", {}).get("results", [])
        return "\n".join([f"- {r['title']}" for r in results])
    except Exception as e:
        return f"Search Error: {str(e)}"

# --- 3. NODES ---
def check_health(state: AgentState):
    ticker = state['ticker'].upper()
    result = check_financial_health(ticker)
    return {"financial_data": result, "status": result['status']}

async def analyze_stock(state: AgentState):
    if state['status'] == "FAIL": return state

    ticker = state['ticker'].upper()
    
    # 1. Run Tools
    news = brave_market_search(ticker)
    chart = get_stock_chart(ticker)
    
    # 2. AI Analysis
    prompt = f"""
    Analyze {ticker}.
    Financials: {state['financial_data']['reason']}
    Real-Time News (Brave): {news}
    
    Give a recommendation (BUY/HOLD/SELL) and detailed reasoning.
    """
    response = await llm.ainvoke([
        SystemMessage(content="You are PrimoGreedy. Sarcastic, skeptical, data-driven."), 
        HumanMessage(content=prompt)
    ])
    
    # 3. Send Email (Only if it passed the firewall)
    email_result = send_email_report(ticker, response.content)
    
    return {
        "final_report": response.content, 
        "chart_data": chart,
        "email_status": email_result
    }

async def chat_mode(state: AgentState):
    response = await llm.ainvoke([HumanMessage(content=state['ticker'])])
    return {"final_report": response.content, "status": "CHAT"}

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