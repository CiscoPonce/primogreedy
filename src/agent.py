import os
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage
import requests
from src.llm import get_llm
from src.finance_tools import check_financial_health

# --- 1. STATE DEFINITION ---
class AgentState(TypedDict):
    ticker: str
    status: str
    financial_data: Optional[dict]
    final_report: Optional[str]

llm = get_llm()

# --- 2. BRAVE SEARCH TOOL ---
def brave_market_search(query: str):
    """Fetches real-time market sentiment using Brave Search API."""
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        return "Brave API Key missing. Skipping web search."
    
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key
    }
    params = {"q": f"{query} stock market news sentiment", "count": 5}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        results = response.json().get("web", {}).get("results", [])
        snippets = [f"- {r['title']}: {r['description']}" for r in results]
        return "\n".join(snippets) if snippets else "No recent news found."
    except Exception as e:
        return f"Search error: {str(e)}"

# --- 3. NODES ---

def check_health(state: AgentState):
    """The Logic Firewall Node."""
    ticker = state['ticker'].upper()
    result = check_financial_health(ticker)
    return {"financial_data": result, "status": result['status']}

async def analyze_stock(state: AgentState):
    """The AI Research Node (Now with Brave Search)."""
    if state['status'] == "FAIL":
        return state

    ticker = state['ticker'].upper()
    # Use Brave for real-time data
    market_news = brave_market_search(ticker)
    
    prompt = f"""
    You are a professional stock analyst.
    Analyze {ticker} based on these financials and real-time news.
    
    Financial Health: {state['financial_data']['reason']}
    Recent Market Sentiment (via Brave): 
    {market_news}
    
    Provide a concise 'Recommendation' (BUY/HOLD/SELL) and a brief justification.
    """
    
    response = await llm.ainvoke([SystemMessage(content="You are a skeptical analyst."), HumanMessage(content=prompt)])
    return {"final_report": response.content}

async def chat_mode(state: AgentState):
    """The Conversational Node."""
    prompt = [
        SystemMessage(content="You are PrimoGreedy, a witty and skeptical financial assistant. Keep it brief."),
        HumanMessage(content=state['ticker'])
    ]
    response = await llm.ainvoke(prompt)
    return {"final_report": response.content, "status": "CHAT"}

# --- 4. THE ROUTER ---
def route_query(state: AgentState):
    query = state['ticker'].strip().upper()
    # Simple logic: If 1-5 chars and no spaces, it's likely a ticker
    if 1 <= len(query) <= 5 and " " not in query:
        return "financial_health_check"
    return "chat_mode"

# --- 5. BUILD THE GRAPH ---
workflow = StateGraph(AgentState)

workflow.add_node("financial_health_check", check_health)
workflow.add_node("analyst_research", analyze_stock)
workflow.add_node("chat_mode", chat_mode)

workflow.set_conditional_entry_point(
    route_query,
    {
        "financial_health_check": "financial_health_check",
        "chat_mode": "chat_mode"
    }
)

workflow.add_edge("financial_health_check", "analyst_research")
workflow.add_edge("analyst_research", END)
workflow.add_edge("chat_mode", END)

app = workflow.compile()

