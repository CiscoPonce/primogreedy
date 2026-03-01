"""Social media handle scouting for stock mentions.

Uses shared core modules for search and ticker validation.
"""

import re
import yfinance as yf

from src.core.logger import get_logger
from src.core.search import brave_search_raw
from src.core.ticker_utils import NOISE_WORDS

logger = get_logger(__name__)

HANDLE_MAP = {
    "DeItaone": "Walter Bloomberg",
    "unusual_whales": "Unusual Whales",
    "JimCramer": "Jim Cramer",
    "CathieDWood": "Cathie Wood",
}


def fetch_tickers_from_social(handle: str) -> list[str]:
    """Search the web for stock mentions by a social media handle.

    Args:
        handle: Twitter/X handle (without @).

    Returns:
        Up to 5 validated ticker symbols.
    """
    search_term = HANDLE_MAP.get(handle, handle)
    query = f'"{search_term}" (stock OR shares OR bought OR sold OR calls OR options)'

    logger.info("Scouting web for: %s...", search_term)

    full_text = brave_search_raw(query, count=15, freshness="pw")
    if not full_text:
        logger.warning("No search results for @%s", handle)
        return []

    raw_candidates = re.findall(r"\b[A-Z]{2,5}\b", full_text)
    unique_tickers = list(dict.fromkeys(c for c in raw_candidates if c not in NOISE_WORDS))

    logger.info("Validating %d candidates for @%s...", len(unique_tickers), handle)

    valid_tickers: list[str] = []
    for ticker in unique_tickers:
        if len(valid_tickers) >= 5:
            break
        try:
            stock = yf.Ticker(ticker)
            price = stock.fast_info.last_price
            if price and price > 0:
                valid_tickers.append(ticker)
        except Exception:
            continue

    logger.info("Found %d valid tickers for @%s: %s", len(valid_tickers), handle, valid_tickers)
    return valid_tickers
