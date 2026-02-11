from typing import TypedDict
from langgraph.graph import StateGraph, END

# Import our tools
from src.finance_tools import check_financial_health
from src.search_tools import get_market_sentiment
from src.llm import get_llm
from langchain_core.messages import SystemMessage, HumanMessage

# 1. Define the "State" (The folder passing between workers)
class AgentState(TypedDict):
    ticker: str
    financial_data: dict
    news_data: str
    final_report: str
    status: str # 'PASS' or 'FAIL'

# 2. NODE A: The Gatekeeper (Firewall)
def financial_filter_node(state: AgentState):
    ticker = state['ticker']
    print(f"\n🚦 FILTER: Checking financials for {ticker}...")
    
    # Run the firewall
    health_result = check_financial_health(ticker)
    
    # Update the state
    return {
        "financial_data": health_result,
        "status": health_result['status']
    }

# 3. NODE B: The Researcher (Search)
def news_search_node(state: AgentState):
    ticker = state['ticker']
    print(f"\n🕵️ RESEARCH: Looking for news on {ticker}...")
    
    # Run the search
    news = get_market_sentiment(ticker)
    
    return {"news_data": news}

# 4. NODE C: The Analyst (LLM)
def report_writer_node(state: AgentState):
    ticker = state['ticker']
    news = state['news_data']
    finances = state['financial_data']
    
    print(f"\n✍️ WRITER: Generating report for {ticker}...")
    
    # The Prompt
    prompt = f"""
    You are a skeptical Wall Street Analyst. 
    
    Here is the financial data for {ticker}: {finances}
    Here is the latest news: {news}
    
    Task: Write a 3-sentence summary.
    1. Start with "RECOMMENDATION: [BUY/SELL/HOLD]"
    2. Explain the financial health (Debt/Cash).
    3. Explain the news sentiment.
    """
    
    # Call the Brain
    llm = get_llm()
    response = llm.invoke([HumanMessage(content=prompt)])
    
    return {"final_report": response.content}

# 5. Build the Graph (The Logic Flow)
workflow = StateGraph(AgentState)

# Add the workers
workflow.add_node("filter", financial_filter_node)
workflow.add_node("researcher", news_search_node)
workflow.add_node("writer", report_writer_node)

# Set the entry point
workflow.set_entry_point("filter")

# Add the "Conditional Edge" (The If/Else Logic)
def check_pass_fail(state):
    if state['status'] == 'FAIL':
        return "end" # Stop immediately
    return "continue"

workflow.add_conditional_edges(
    "filter",
    check_pass_fail,
    {
        "end": END,
        "continue": "researcher"
    }
)

# Connect Researcher to Writer
workflow.add_edge("researcher", "writer")
workflow.add_edge("writer", END)

# Compile the machine
app = workflow.compile()