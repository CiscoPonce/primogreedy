import json
import os
import time
import requests
from .logger import get_logger

logger = get_logger(__name__)

SEEN_TICKERS_FILE = "seen_tickers.json"
MEMORY_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

# VPS Data API (optional — falls back to local JSON if not set)
VPS_API_URL = os.getenv("VPS_API_URL", "").rstrip("/")
VPS_API_KEY = os.getenv("VPS_API_KEY", "")


def _vps_headers() -> dict:
    return {"X-API-Key": VPS_API_KEY, "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_seen_tickers() -> dict[str, float]:
    """Load the seen-tickers ledger, pruning entries older than 30 days.

    Values are Unix timestamps (float).
    Tries VPS API first, falls back to local JSON file.
    """
    if VPS_API_URL:
        try:
            resp = requests.get(f"{VPS_API_URL}/seen-tickers", headers=_vps_headers(), timeout=5)
            resp.raise_for_status()
            data = resp.json()
            logger.debug("Loaded %d seen tickers from VPS", len(data))
            return data
        except Exception as exc:
            logger.warning("VPS seen-tickers unavailable, using local fallback: %s", exc)

    return _load_local()


def mark_ticker_seen(ticker: str, region: str = "USA") -> None:
    """Record a ticker as recently analysed."""
    if VPS_API_URL:
        try:
            resp = requests.post(
                f"{VPS_API_URL}/seen-tickers",
                headers=_vps_headers(),
                json={"ticker": ticker, "region": region},
                timeout=5,
            )
            resp.raise_for_status()
            logger.debug("Marked %s as seen on VPS", ticker)
            return
        except Exception as exc:
            logger.warning("VPS mark_ticker_seen failed, using local fallback: %s", exc)

    # Local fallback
    data = _load_local()
    data[ticker] = time.time()
    _save(data)


# ---------------------------------------------------------------------------
# Local JSON fallback (original behavior)
# ---------------------------------------------------------------------------

def _load_local() -> dict[str, float]:
    if not os.path.exists(SEEN_TICKERS_FILE):
        return {}
    try:
        with open(SEEN_TICKERS_FILE, "r") as f:
            raw: dict = json.load(f)

        now = time.time()
        cleaned: dict[str, float] = {}
        for ticker, ts in raw.items():
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


def _save(data: dict) -> None:
    try:
        with open(SEEN_TICKERS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError as exc:
        logger.error("Failed to write %s: %s", SEEN_TICKERS_FILE, exc)
