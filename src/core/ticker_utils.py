import re
import yfinance as yf
from .logger import get_logger

logger = get_logger(__name__)

REGION_SUFFIXES = {
    "USA": [""],
    "UK": [".L"],
    "Canada": [".TO", ".V"],
    "Australia": [".AX"],
}

NOISE_WORDS = frozenset({
    "THE", "AND", "FOR", "ARE", "NOT", "YOU", "ALL", "CAN", "ONE", "OUT",
    "HAS", "NEW", "NOW", "SEE", "WHO", "GET", "SHE", "TOO", "USE", "NONE",
    "THIS", "THAT", "WITH", "HAVE", "FROM", "THEY", "BEEN", "SAID", "MAKE",
    "LIKE", "JUST", "OVER", "SUCH", "TAKE", "YEAR", "SOME", "MOST", "VERY",
    "WHEN", "WHAT", "YOUR", "ALSO", "INTO", "ROLE", "TASK", "INPUT", "STOCK",
    "TICKER", "CAP", "MICRO", "NANO", "CEO", "CFO", "BUY", "SELL", "LOW",
    "HIGH", "ATH", "ETF", "USA", "USD", "YTD", "TOP", "HOT", "BEST", "LIVE",
    "DATA", "GDP", "CPI", "FED", "FOMC", "PCE", "PPI", "CNBC", "NYSE",
    "NASDAQ", "NEWS", "REAL", "TIME", "TODAY", "WSJ", "SEC", "WHY", "IPO",
    "GBP", "EUR", "EPS", "FYI", "AGM",
})


def extract_tickers(text: str) -> list[str]:
    """Extract plausible ticker symbols from free-form text.

    Handles comma-separated LLM output, cashtags ($AAPL), and bare
    uppercase words.  Filters out common English noise.

    Returns a deduplicated list preserving discovery order.
    """
    cleaned = text.strip().upper()

    # Try comma-separated first (LLM extraction output)
    if "," in cleaned:
        parts = [re.sub(r"[^A-Z.]", "", p) for p in cleaned.split(",")]
    else:
        parts = re.findall(r"\b([A-Z]{2,5}(?:\.[A-Z]{1,2})?)\b", cleaned)

    seen: set[str] = set()
    result: list[str] = []
    for t in parts:
        if len(t) < 2 or t in NOISE_WORDS or t in seen:
            continue
        seen.add(t)
        result.append(t)
    return result


def resolve_ticker_suffix(raw_ticker: str, region: str) -> str:
    """Append the correct exchange suffix for non-US regions.

    Tries each known suffix and validates with yFinance.
    """
    if "." in raw_ticker:
        return raw_ticker

    suffixes = REGION_SUFFIXES.get(region, [""])
    if suffixes == [""]:
        return raw_ticker

    for suffix in suffixes:
        candidate = f"{raw_ticker}{suffix}"
        try:
            info = yf.Ticker(candidate).info
            if info.get("marketCap", 0) > 0:
                logger.debug("Suffix resolved: %s -> %s", raw_ticker, candidate)
                return candidate
        except Exception:
            continue
    return raw_ticker


def normalize_price(price: float, ticker: str, currency: str = "USD") -> float:
    """Convert UK pence to pounds when needed."""
    if ticker.endswith(".L") or currency in ("GBp", "GBX"):
        return price / 100
    return price
