from typing import Dict, Any, Optional
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import RetryPolicy
from .state import AgentState, create_initial_state
from ..agents.data_collection_agent import data_collection_agent_node
from ..agents.technical_analysis_agent import technical_analysis_agent_node
from ..agents.news_intelligence_agent import news_intelligence_agent_node
from ..agents.portfolio_manager_agent import portfolio_manager_agent_node


def _log_partial(updates: dict, agent_name: str) -> None:
    """Log interesting fields from a partial-state update dict."""
    print(f"\n{agent_name} Agent Complete:")

    if agent_name == "Data Collection":
        data_results = updates.get('data_collection_results')
        if data_results:
            market_data = data_results.get('market_data', {})
            print(f"Current Price: ${market_data.get('current_price', 'N/A')}")

    elif agent_name == "Technical Analysis":
        tech_results = updates.get('technical_analysis_results')
        if tech_results:
            print(f"Technical Success: {tech_results.get('success', False)}")

    elif agent_name == "News Intelligence":
        news_results = updates.get('news_intelligence_results')
        if news_results:
            print(f"News Success: {news_results.get('success', False)}")

    elif agent_name == "Portfolio Manager":
        portfolio_results = updates.get('portfolio_manager_results', {})
        for sym, sym_data in portfolio_results.items():
            if sym_data and sym_data.get('success'):
                print(f"Signal: {sym_data.get('trading_signal', 'N/A')} | "
                      f"Confidence: {sym_data.get('confidence_level', 'N/A')}")

    if updates.get('error'):
        print(f"Error: {updates['error']}")


async def debug_data_collection_node(state: AgentState) -> dict:
    """Data collection node with debug output."""
    updates = await data_collection_agent_node(state)
    _log_partial(updates, "Data Collection")
    return updates


async def debug_technical_analysis_node(state: AgentState) -> dict:
    """Technical analysis node with debug output."""
    updates = await technical_analysis_agent_node(state)
    _log_partial(updates, "Technical Analysis")
    return updates


async def debug_news_intelligence_node(state: AgentState) -> dict:
    """News intelligence node with debug output."""
    updates = await news_intelligence_agent_node(state)
    _log_partial(updates, "News Intelligence")
    return updates


async def debug_portfolio_manager_node(state: AgentState) -> dict:
    """Portfolio manager node with debug output."""
    updates = await portfolio_manager_agent_node(state)
    _log_partial(updates, "Portfolio Manager")
    return updates


def create_workflow() -> StateGraph:
    """
    Create LangGraph workflow connecting all agents.
            
        Returns:
        StateGraph: Configured workflow graph
    """
    # Initialize workflow
    _api_retry = RetryPolicy(max_attempts=3, initial_interval=2.0)

    workflow = StateGraph(AgentState)
    
    workflow.add_node("data_collection", debug_data_collection_node, retry=_api_retry)
    workflow.add_node("technical_analysis", debug_technical_analysis_node, retry=_api_retry)
    workflow.add_node("news_intelligence", debug_news_intelligence_node, retry=_api_retry)
    workflow.add_node("portfolio_manager", debug_portfolio_manager_node, retry=_api_retry)
    
    # Define linear flow
    workflow.add_edge(START, "data_collection")
    workflow.add_edge("data_collection", "technical_analysis")
    workflow.add_edge("technical_analysis", "news_intelligence")
    workflow.add_edge("news_intelligence", "portfolio_manager")
    workflow.add_edge("portfolio_manager", END)
    
    return workflow


async def run_analysis(symbols: list[str], session_id: str = "default", analysis_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Run complete analysis workflow for symbols.
        
        Args:
            symbols: List of stock symbols to analyze
        session_id: Session identifier
        analysis_date: Date for analysis in YYYY-MM-DD format (optional, defaults to today)
            
        Returns:
        Dict with analysis results
        """
    try:
        # Create workflow
        workflow = create_workflow()
        app = workflow.compile(checkpointer=InMemorySaver())
        
        # Initialize state with analysis date
        initial_state = create_initial_state(session_id, symbols, analysis_date)
        
        # Run workflow
        config = {"configurable": {"thread_id": session_id}, "recursion_limit": 30}
        result = await app.ainvoke(initial_state, config)
        
        # Extract results
        return {
            'success': True,
            'session_id': session_id,
            'symbols': symbols,
            'analysis_date': analysis_date,
            'results': {
                'data_collection': result.get('data_collection_results'),
                'technical_analysis': result.get('technical_analysis_results'),
                'news_intelligence': result.get('news_intelligence_results'),
                'portfolio_manager': result.get('portfolio_manager_results')
            },
            'final_step': result.get('current_step'),
            'error': result.get('error')
        }
        
    except Exception as e:
        print(f"Workflow error: {e}")
        return {
            'success': False,
            'error': str(e),
            'symbols': symbols,
            'session_id': session_id,
            'analysis_date': analysis_date
        }


def should_continue(state: AgentState) -> str:
    """
    Simple conditional logic for workflow routing.
    
    Args:
        state: Current workflow state
        
    Returns:
        Next step or END
    """
    if state.get('error'):
        return END
    
    current_step = state.get('current_step', '')
    
    if current_step == 'data_collection_complete':
        return 'technical_analysis'
    elif current_step == 'technical_analysis_complete':
        return 'news_intelligence'
    elif current_step == 'news_intelligence_complete':
        return 'portfolio_manager'
    elif current_step == 'portfolio_management_complete':
        return END
    else:
        return END 