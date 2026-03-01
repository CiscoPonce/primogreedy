import json
import os
import time
from .logger import get_logger

logger = get_logger(__name__)

SEEN_TICKERS_FILE = "seen_tickers.json"
MEMORY_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


def load_seen_tickers() -> dict[str, float]:
    """Load the seen-tickers ledger, pruning entries older than 30 days.

    Values are Unix timestamps (float).
    """
    if not os.path.exists(SEEN_TICKERS_FILE):
        return {}
    try:
        with open(SEEN_TICKERS_FILE, "r") as f:
            raw: dict = json.load(f)

        now = time.time()
        cleaned: dict[str, float] = {}
        for ticker, ts in raw.items():
            # Support both unix timestamps and ISO strings (legacy)
            if isinstance(ts, str):
                try:
                    from datetime import datetime, timezone
                    ts = datetime.fromisoformat(ts).timestamp()
                except ValueError:
                    continue
            if now - ts < MEMORY_TTL_SECONDS:
                cleaned[ticker] = ts

        _save(cleaned)
        return cleaned

    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s", SEEN_TICKERS_FILE, exc)
        return {}


def mark_ticker_seen(ticker: str) -> None:
    """Record a ticker as recently analysed."""
    data = load_seen_tickers()
    data[ticker] = time.time()
    _save(data)


def _save(data: dict) -> None:
    try:
        with open(SEEN_TICKERS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError as exc:
        logger.error("Failed to write %s: %s", SEEN_TICKERS_FILE, exc)
