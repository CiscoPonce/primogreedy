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
    "GBP", "EUR", "EPS", "FYI", "AGM", "RSI", "PE", "PB", "ROE", "ROI",
    "API", "ETN", "OTC", "ADR", "DMA", "EMA", "SMA", "MACD", "IPO",
    "LLC", "INC", "LTD", "PLC", "CORP", "FAQ", "PDF", "URL", "EST",
    "PST", "UTC", "CEO", "COO", "CTO", "CFO", "CMO", "CSO",
    "FUND", "BOND", "CASH", "DEBT", "EARN", "GAIN", "LOSS", "RISK",
    "WEEK", "DAYS", "RATE", "MOVE", "HOLD", "CALL", "DEEP", "NEXT",
    "HUGE", "RARE", "PICK", "ONLY", "FIND", "LIST", "MORE", "EACH",
    "MUCH", "MANY", "SAME", "FULL", "LONG", "LOOK", "MEAN", "EVEN",
    "BOTH", "GOOD", "WELL", "BACK", "SHOW", "HELP", "KEEP", "DOWN",
    "TURN", "COME", "WILL", "BEEN", "WERE", "THAN", "THEM", "THEN",
    "AMID", "PAST", "FREE", "LAST", "DOES", "WENT", "NEAR", "GAVE",
    "RUN", "SAY", "WAY", "MAY", "HAD", "GOT", "OUR", "ITS", "HIS",
    "HER", "ANY", "FEW", "DID", "ASK", "OWN", "OLD", "BIG", "DAY",
    "PER", "SET", "TRY", "LET", "PUT", "END", "ADD", "PAY",
    "OF", "OR", "IF", "IN", "ON", "AT", "TO", "UP", "BY", "SO", "NO",
    "DO", "AS", "AN", "IS", "IT", "BE", "WE", "GO", "MY", "VS",
    # Financial acronyms / index names that aren't tradeable tickers
    "ROCE", "FTSE", "DJIA", "EBIT", "WACC", "CAGR", "ROIC", "REIT",
    "SPAC", "NBER", "OPEC", "MSCI", "EMEA", "APAC", "OECD", "FIFO",
    "FINRA", "SIPC", "FDIC", "LISA", "ISA", "ATM", "AMA", "FDA",
    "PHNX", "IPG", "GAAP", "IFRS", "FASB", "IASB", "PCAOB",
    "THING", "TXTW", "MRC", "HERE", "ELSE", "SURE", "WORK",
    "SAFE", "IDEA", "PLAN", "RULE", "STEP", "PLAY", "OPEN",
    "PART", "NOTE", "LINE", "READ", "FILL", "SIZE", "WIDE",
    "SIGN", "RISE", "LEAD", "PUSH", "PULL", "DROP", "JUMP",
    "AEDT", "AEST", "BEST", "FAST", "EVER", "FORM", "SENT",
    "GROW", "MARK", "PURE", "REAL", "SOFT", "TALK", "VOTE",
})

_MAX_TICKER_LEN = 8  # longest valid ticker with suffix: e.g. CHE.UN.TO


def _find_ticker_tokens(text: str) -> list[str]:
    """Find tokens in mixed-case text that look like stock tickers.

    Only matches words that are ALREADY fully uppercase in the source —
    real tickers in financial text are written as "AAPL" or "$MSFT" while
    normal English words appear in mixed case.
    """
    cashtags = re.findall(r"\$([A-Z]{1,5})", text)

    # Uppercase words bounded by non-letter chars.  The negative look-arounds
    # ensure we skip uppercase letters inside normal words (e.g. "Apple").
    bare = re.findall(
        r"(?<![A-Za-z])([A-Z]{2,5}(?:\.[A-Z]{1,3})?)(?![a-zA-Z])", text
    )

    return cashtags + bare


def extract_tickers(text: str) -> list[str]:
    """Extract plausible ticker symbols from free-form text.

    Two modes:
      1. **Comma-separated LLM output** ("AAPL, MSFT, GOOG") — only when
         the comma-parts are short, indicating an actual ticker list.
      2. **Mixed-case prose** (Brave search results, articles) — scans for
         words that are already fully uppercase, which is how tickers
         naturally appear in financial text.

    Returns a deduplicated list preserving discovery order.
    """
    cleaned = text.strip()

    if "," in cleaned:
        parts = [p.strip() for p in cleaned.split(",")]
        short_parts = sum(1 for p in parts if len(p.split()) <= 2 and len(p) <= 12)

        if short_parts > len(parts) * 0.5:
            candidates = [re.sub(r"[^A-Z.]", "", p.upper()) for p in parts]
        else:
            candidates = _find_ticker_tokens(cleaned)
    else:
        candidates = _find_ticker_tokens(cleaned)

    seen: set[str] = set()
    result: list[str] = []
    for t in candidates:
        if not t or len(t) < 2 or len(t) > _MAX_TICKER_LEN:
            continue
        if t in NOISE_WORDS or t in seen:
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
