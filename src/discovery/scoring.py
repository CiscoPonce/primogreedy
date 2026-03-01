"""Candidate scoring and ranking (Proposal D).

Batch-scores screened candidates on quantitative criteria so the expensive
LLM analyst step only runs on the best picks.
"""

from src.core.logger import get_logger
from src.finance_tools import calculate_graham_number

logger = get_logger(__name__)


def score_candidate(candidate: dict) -> dict:
    """Score a single candidate dict (as returned by ``screen_microcaps``).

    Returns the same dict with ``score`` (0-100) and ``score_breakdown``
    keys added.
    """
    score = 0
    breakdown: list[str] = []

    price = candidate.get("price", 0)
    eps = candidate.get("eps", 0)
    bv = candidate.get("book_value", 0)
    fcf = candidate.get("free_cashflow", 0)
    ebitda = candidate.get("ebitda", 0)
    total_debt = candidate.get("total_debt", 0)
    total_cash = candidate.get("total_cash", 0)
    current_ratio = candidate.get("current_ratio", 0)
    pb = candidate.get("price_to_book", 0)

    # 1. Profitability (20 pts)
    if eps and eps > 0:
        score += 20
        breakdown.append("+20 profitable (EPS > 0)")

    # 2. Graham undervaluation (25 pts)
    info_proxy = {"trailingEps": eps, "bookValue": bv}
    graham = calculate_graham_number(info_proxy)
    if graham > 0 and price > 0:
        margin = (graham - price) / graham
        if margin > 0.3:
            score += 25
            breakdown.append(f"+25 Graham margin {margin:.0%}")
        elif margin > 0:
            pts = int(margin * 50)
            score += pts
            breakdown.append(f"+{pts} Graham margin {margin:.0%}")

    # 3. Price-to-Book deep value (15 pts)
    if pb and 0 < pb < 1.0:
        score += 15
        breakdown.append(f"+15 P/B={pb:.2f} < 1.0")
    elif pb and 0 < pb < 1.5:
        score += 8
        breakdown.append(f"+8 P/B={pb:.2f} < 1.5")

    # 4. Free cash flow positive (15 pts)
    if fcf and fcf > 0:
        score += 15
        breakdown.append("+15 FCF positive")

    # 5. Low debt burden (10 pts)
    if ebitda and ebitda > 0 and total_debt is not None:
        net_debt_ebitda = (total_debt - (total_cash or 0)) / ebitda
        if net_debt_ebitda < 1.0:
            score += 10
            breakdown.append(f"+10 low debt ({net_debt_ebitda:.1f}x)")
        elif net_debt_ebitda < 2.5:
            score += 5
            breakdown.append(f"+5 moderate debt ({net_debt_ebitda:.1f}x)")

    # 6. Liquidity (10 pts)
    if current_ratio and current_ratio > 1.5:
        score += 10
        breakdown.append(f"+10 liquid (CR={current_ratio:.1f})")
    elif current_ratio and current_ratio > 1.0:
        score += 5
        breakdown.append(f"+5 adequate liquidity (CR={current_ratio:.1f})")

    # 7. Cash runway for unprofitable companies (5 pts)
    if eps <= 0 and fcf and fcf < 0 and total_cash:
        runway_years = total_cash / abs(fcf)
        if runway_years >= 2:
            score += 5
            breakdown.append(f"+5 runway {runway_years:.1f}y")

    candidate["score"] = score
    candidate["score_breakdown"] = breakdown
    return candidate


def rank_candidates(candidates: list[dict], top_n: int = 5) -> list[dict]:
    """Score and sort candidates, returning the top N.

    Each candidate dict gets ``score`` and ``score_breakdown`` added.
    """
    scored = [score_candidate(c) for c in candidates]
    scored.sort(key=lambda c: c["score"], reverse=True)

    for i, c in enumerate(scored[:top_n]):
        logger.info(
            "Rank #%d: %s  score=%d  %s",
            i + 1,
            c["ticker"],
            c["score"],
            " | ".join(c["score_breakdown"]),
        )

    return scored[:top_n]
