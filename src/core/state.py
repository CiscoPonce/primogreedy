import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    """Shared state schema for all LangGraph pipelines.

    ``total=False`` lets nodes return partial updates.
    """
    region: str
    ticker: str
    candidates: Annotated[list, operator.add]
    company_name: str
    market_cap: float
    is_small_cap: bool
    financial_data: dict
    final_verdict: str
    retry_count: int
    status: str
    final_report: str
    chart_data: bytes
    manual_search: bool
    candidate_scores: Annotated[list, operator.add]
    bull_case: str
    bear_case: str
    debate_used: bool
