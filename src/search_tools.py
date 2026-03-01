"""Market sentiment search tool.

Thin wrapper around the shared Brave search for backward compatibility.
"""

from src.core.search import brave_search
from src.core.logger import get_logger

logger = get_logger(__name__)


def get_market_sentiment(ticker: str) -> str:
    """Search for recent news and sentiment about a ticker."""
    logger.info("Searching sentiment for: %s", ticker)
    return brave_search(f"{ticker} stock news risks analysis", count=3)
