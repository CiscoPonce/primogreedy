from typing import TypedDict


class AgentState(TypedDict, total=False):
    """Shared state schema for all LangGraph pipelines.

    ``total=False`` lets nodes return partial updates.
    """
    region: str
    ticker: str
    candidates: list
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
    candidate_scores: list  # [{ticker, score, metrics}, ...]
