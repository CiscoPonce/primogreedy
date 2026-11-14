"""Candidate scoring and ranking (Proposal D).

Batch-scores screened candidates on quantitative criteria so the expensive
LLM analyst step only runs on the best picks.

Scoring now also folds in *insider buying* — the single strongest known
alpha signal in micro-cap investing — via a cached lookup so scoring a whole
candidate list costs at most a handful of API calls.
"""

import os
import time
from src.core.logger import get_logger
from src.finance_tools import calculate_graham_number

logger = get_logger(__name__)

# Insider signal cache: ticker -> result dict (avoid re-fetching within a run).
_INSIDER_CACHE: dict[str, dict] = {}
_ATTR_CACHE: dict = {"by_region": None, "ts": 0.0}
_ATTR_TTL = 600


def _infer_region(ticker: str) -> str:
    upper = (ticker or "").upper()
    if upper.endswith(".L"):
        return "UK"
    if upper.endswith((".TO", ".V", ".CN", ".NE")):
        return "Canada"
    if upper.endswith(".AX"):
        return "Australia"
    if "." in upper:
        return "Other"
    return "USA"


def fetch_attribution_weights() -> dict[str, dict]:
    """Fetch regional win/return attribution from the VPS API (TTL-cached)."""
    now = time.time()
    if _ATTR_CACHE["by_region"] is not None and now - _ATTR_CACHE["ts"] < _ATTR_TTL:
        return _ATTR_CACHE["by_region"]
    vps_url = os.getenv("VPS_API_URL", "").rstrip("/")
    if not vps_url:
        return {}
    try:
        import requests
        headers = {}
        if os.getenv("VPS_API_KEY"):
            headers["X-API-Key"] = os.getenv("VPS_API_KEY")
        resp = requests.get(f"{vps_url}/portfolio/attribution", headers=headers, timeout=8)
        if resp.ok:
            by_region = resp.json().get("by_region") or {}
            _ATTR_CACHE["by_region"] = by_region
            _ATTR_CACHE["ts"] = now
            return by_region
    except Exception as exc:
        logger.debug("Attribution fetch skipped: %s", exc)
    return {}


def _attribution_adjustment(ticker: str, by_region: dict[str, dict]) -> tuple[int, str | None]:
    """Return bounded (-8..+8) score adjustment from historical regional win rate."""
    stats = by_region.get(_infer_region(ticker))
    if not stats or (stats.get("priced_count") or 0) < 5:
        return 0, None
    win_rate = stats.get("win_rate_pct", 0.0)
    avg_ret = stats.get("avg_return_pct", 0.0)
    adj = 0
    if win_rate >= 45.0 or avg_ret >= 20.0:
        adj = 6 if (win_rate >= 45.0 and avg_ret >= 20.0) else 4
    elif win_rate < 20.0 and avg_ret < -10.0:
        adj = -6
    elif avg_ret < 0:
        adj = -3
    if adj == 0:
        return 0, None
    region = _infer_region(ticker)
    sign = "+" if adj > 0 else ""
    return adj, f"{sign}{adj} {region} attribution ({win_rate:.0f}% win, {avg_ret:+.1f}% avg)"


def _insider_signal(ticker: str) -> dict | None:
    """Cached insider buy/sell lookup.  Never raises."""
    if ticker in _INSIDER_CACHE:
        return _INSIDER_CACHE[ticker]
    try:
        from src.discovery.insider_feed import get_insider_buys
        result = get_insider_buys(ticker)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Insider lookup failed for %s: %s", ticker, exc)
        result = None
    _INSIDER_CACHE[ticker] = result
    return result


def score_candidate(candidate: dict, use_insider: bool = True,
                    use_attribution: bool = True) -> dict:
    """Score a single candidate dict (as returned by ``screen_microcaps``).

    Returns the same dict with ``score`` (0-100), ``score_breakdown`` and
    ``insider_signal`` keys added.
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

    # 8. Insider buying signal (up to 20 pts) — strongest micro-cap alpha.
    insider_signal = None
    if use_insider:
        insider_signal = _insider_signal(candidate.get("ticker", ""))
        score += _score_insider(insider_signal, breakdown)

    # 9. Tradeable-liquidity bonus (up to 5 pts) — reward genuinely liquid
    #    names so they out-rank illiquid ones that are harder to act on.
    dollar_vol = candidate.get("dollar_volume") or 0
    if dollar_vol >= 1_000_000:
        score += 5
        breakdown.append("+5 highly liquid (>$1M/day)")
    elif dollar_vol >= 250_000:
        score += 3
        breakdown.append("+3 liquid (>$250k/day)")

    # 10. Empirical regional attribution adjustment (-6..+6 pts)
    if use_attribution:
        by_region = fetch_attribution_weights()
        if by_region:
            adj, reason = _attribution_adjustment(candidate.get("ticker", ""), by_region)
            if adj:
                score += adj
                breakdown.append(reason)

    candidate["score"] = score
    candidate["score_breakdown"] = breakdown
    candidate["insider_signal"] = insider_signal
    return candidate


def _score_insider(signal: dict | None, breakdown: list[str]) -> int:
    """Turn an insider-signal dict into points (0-20) and a breakdown line."""
    if not signal:
        return 0
    sentiment = str(signal.get("sentiment", ""))
    net = signal.get("change", 0) or 0

    if "Bullish" in sentiment:
        pts = 20 if net >= 500_000 else 12
        breakdown.append(f"+{pts} insider buying (net {net:,} shares)")
        return pts
    if "Bearish" in sentiment:
        # Net insider selling is a real warning; penalise modestly.
        breakdown.append(f"-5 insider selling (net {net:,} shares)")
        return -5
    return 0


def rank_candidates(candidates: list[dict], top_n: int = 5,
                    use_insider: bool = True) -> list[dict]:
    """Score and sort candidates, returning the top N.

    Each candidate dict gets ``score`` and ``score_breakdown`` added.
    """
    scored = [score_candidate(c, use_insider=use_insider) for c in candidates]
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
