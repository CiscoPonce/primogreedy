"""Trending stock scanner using Brave Search.

Uses shared core modules for search and ticker validation.
"""

import random
import re
import yfinance as yf

from src.core.logger import get_logger
from src.core.search import brave_search_raw
from src.core.ticker_utils import NOISE_WORDS

logger = get_logger(__name__)

SEARCH_QUERIES = [
    "stock market top trending gainers today",
    "most active stocks by volume today",
    "undervalued growth stocks 2026",
    "stocks with highest implied volatility today",
    "best performing tech stocks this week",
    "small cap stocks breaking out today",
    "unusual options activity tickers today",
]


def get_trending_stocks() -> list[str]:
    """Search the web for trending stocks, validate with yFinance.

    Returns up to 5 validated ticker symbols.
    """
    selected_query = random.choice(SEARCH_QUERIES)
    logger.info("Scanner strategy: '%s'", selected_query)

    full_text = brave_search_raw(selected_query, count=20, freshness="pw")
    if not full_text:
        logger.warning("Scanner got no search results")
        return ["NVDA", "TSLA", "AMD"]

    candidates = re.findall(r"\b[A-Z]{2,5}\b", full_text)
    random.shuffle(candidates)

    logger.info("Scanning %d raw candidates...", len(candidates))

    unique_tickers: list[str] = []
    seen: set[str] = set()

    for ticker in candidates:
        if ticker in NOISE_WORDS or ticker in seen:
            continue
        if len(unique_tickers) >= 5:
            break

        try:
            stock = yf.Ticker(ticker)
            price = stock.fast_info.last_price
            if price and price > 0:
                unique_tickers.append(ticker)
                seen.add(ticker)
        except Exception:
            continue

    return unique_tickers if unique_tickers else ["NVDA", "TSLA", "AMD"]
