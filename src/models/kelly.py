"""Kelly Criterion position sizing — pure-function module.

Computes optimal position size from historical trade performance and
verdict strength.  No API calls or side effects; all data comes from
the portfolio tracker layer.
"""

import json
import os
import time
from dataclasses import dataclass

import requests
import yfinance as yf

from src.core.logger import get_logger
from src.core.ticker_utils import normalize_price

logger = get_logger(__name__)

PORTFOLIO_FILE = "paper_portfolio.json"
VPS_API_URL = os.getenv("VPS_API_URL", "").rstrip("/")
VPS_API_KEY = os.getenv("VPS_API_KEY", "")

_MIN_TRADES_FOR_KELLY = 5

_cache: dict = {"stats": None, "ts": 0}
_CACHE_TTL = 600  # 10 minutes — enough for an entire cron run

_VERDICT_SCALE = {
    "STRONG BUY": 1.0,
    "BUY": 0.7,
    "WATCH": 0.3,
}
_POS_FLOOR = 1.0
_POS_CAP = 25.0
_VERDICT_CAPS = {
    "STRONG BUY": 25.0,
    "BUY": 15.0,
    "WATCH": 5.0,
}
_MAX_KELLY_FRACTION = 0.5  # cap raw Kelly to avoid extreme values


@dataclass
class KellyStats:
    """Summary statistics required for Kelly sizing."""

    total_trades: int
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    kelly_fraction: float
    half_kelly: float


# ---------------------------------------------------------------------------
# Data retrieval helpers
# ---------------------------------------------------------------------------

def _trades_from_vps() -> list[dict] | None:
    """Fetch evaluated trades from VPS ``/portfolio/evaluate``."""
    if not VPS_API_URL:
        return None
    try:
        resp = requests.get(
            f"{VPS_API_URL}/portfolio/evaluate",
            headers={"X-API-Key": VPS_API_KEY},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("trades", [])
    except Exception as exc:
        logger.warning("VPS portfolio fetch failed: %s", exc)
        return None


def _trades_from_local() -> list[dict]:
    """Evaluate trades from the local ``paper_portfolio.json``."""
    if not os.path.exists(PORTFOLIO_FILE):
        return []
    try:
        with open(PORTFOLIO_FILE, "r") as f:
            portfolio = json.load(f)
    except Exception:
        return []

    trades = []
    for t in portfolio:
        ticker = t["ticker"]
        entry = t.get("entry_price", 0)
        if entry <= 0:
            continue
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            price = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0
            currency = info.get("currency", "USD")
            price = normalize_price(price, ticker, currency)
            if price > 0:
                gain_pct = ((price - entry) / entry) * 100
                trades.append({
                    "ticker": ticker,
                    "entry": entry,
                    "current": price,
                    "gain_pct": gain_pct,
                    "verdict": t.get("verdict", ""),
                })
        except Exception:
            continue
    return trades


# ---------------------------------------------------------------------------
# Kelly stats calculation  (Task 4.1)
# ---------------------------------------------------------------------------

def get_kelly_stats() -> KellyStats:
    """Compute Kelly Criterion inputs from historical portfolio data.

    Results are cached for ``_CACHE_TTL`` seconds so that multiple
    analyst_node calls within a single cron run don't each trigger
    expensive live-price lookups for the entire portfolio.

    Tries the VPS endpoint first, falls back to local JSON.
    Returns conservative defaults (``half_kelly=0``) when fewer than
    ``_MIN_TRADES_FOR_KELLY`` trades exist.
    """
    if _cache["stats"] is not None and (time.time() - _cache["ts"]) < _CACHE_TTL:
        return _cache["stats"]

    trades = _trades_from_vps()
    if trades is None:
        trades = _trades_from_local()

    valid = [t for t in trades if t.get("gain_pct") is not None]
    total = len(valid)

    if total < _MIN_TRADES_FOR_KELLY:
        result = KellyStats(
            total_trades=total,
            win_rate=0.0,
            avg_win_pct=0.0,
            avg_loss_pct=0.0,
            kelly_fraction=0.0,
            half_kelly=0.0,
        )
        _cache["stats"] = result
        _cache["ts"] = time.time()
        return result

    winners = [t["gain_pct"] for t in valid if t["gain_pct"] > 0]
    losers = [abs(t["gain_pct"]) for t in valid if t["gain_pct"] <= 0]

    win_rate = len(winners) / total if total else 0.0
    avg_win = (sum(winners) / len(winners)) if winners else 0.0
    avg_loss = (sum(losers) / len(losers)) if losers else 0.0

    # Kelly formula using decimal returns
    avg_win_dec = avg_win / 100
    avg_loss_dec = avg_loss / 100

    if avg_loss_dec > 0 and avg_win_dec > 0:
        kelly = (win_rate / avg_loss_dec) - ((1 - win_rate) / avg_win_dec)
    else:
        kelly = 0.0

    kelly = max(min(kelly, _MAX_KELLY_FRACTION), 0.0)

    result = KellyStats(
        total_trades=total,
        win_rate=win_rate,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        kelly_fraction=kelly,
        half_kelly=kelly / 2,
    )
    _cache["stats"] = result
    _cache["ts"] = time.time()
    logger.info(
        "Kelly stats: %d trades, %.0f%% win rate, half-Kelly=%.4f",
        total, win_rate * 100, kelly / 2,
    )
    return result


# ---------------------------------------------------------------------------
# Position size calculator  (Task 4.2)
# ---------------------------------------------------------------------------

def calculate_position_size(stats: KellyStats, verdict: str) -> float:
    """Return position size as percentage of portfolio (0-100).

    Applies half-Kelly with verdict-based scaling:
      STRONG BUY -> 100% of half-Kelly
      BUY        -> 70% of half-Kelly
      WATCH      -> 30% of half-Kelly
    Clamped to [1%, 25%] to prevent over-concentration.
    Returns 0.0 for AVOID or when insufficient data.
    """
    if verdict == "AVOID" or stats.half_kelly <= 0:
        return 0.0

    scale = _VERDICT_SCALE.get(verdict, 0.0)
    if scale == 0.0:
        return 0.0

    raw = stats.half_kelly * scale * 100
    cap = _VERDICT_CAPS.get(verdict, _POS_CAP)

    return round(max(_POS_FLOOR, min(raw, cap)), 1)
