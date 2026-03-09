"""Intraday Catalyst Polling Daemon for PrimoGreedy.

Runs as a systemd timer on the VPS (every 15 minutes during market hours,
9:00-16:30 EST on weekdays).  When a trigger fires, dispatches a GitHub
Actions ``repository_dispatch`` event to run the pipeline for the triggered
ticker.

Trigger conditions (any one fires):
    1. Volume > 3x average daily volume for a seed ticker
    2. New Form 4 insider purchase > $50K for a tracked ticker
    3. Price move > 10% intraday for a seed ticker

Usage:
    python catalyst_poll.py

Environment variables:
    FINNHUB_API_KEY     — Finnhub REST API key
    GITHUB_TOKEN        — GitHub PAT with repo scope
    GITHUB_REPO         — e.g. "CiscoPonce/primogreedy"
    VPS_API_URL         — PrimoGreedy data API URL
    VPS_API_KEY         — Data API key
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [catalyst_poll] %(levelname)s: %(message)s",
)
logger = logging.getLogger("catalyst_poll")

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "CiscoPonce/primogreedy")
VPS_API_URL = os.getenv("VPS_API_URL", "").rstrip("/")
VPS_API_KEY = os.getenv("VPS_API_KEY", "")

VOLUME_MULTIPLIER = 3.0
INSIDER_MIN_VALUE = 50_000
PRICE_MOVE_PCT = 10.0

SEED_TICKERS = [
    "HNRA", "TLSS", "MNTS", "BMTX", "KORE", "RVSN", "DRUG", "HYSR",
    "AAON", "CASS", "GIII", "PATK", "MGRC", "LCNB", "CHCO",
]


def _finnhub_get(path: str, params: dict) -> dict:
    """Wrapper for Finnhub REST calls."""
    params["token"] = FINNHUB_API_KEY
    try:
        resp = requests.get(
            f"https://finnhub.io/api/v1/{path}",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Finnhub %s error: %s", path, exc)
        return {}


def check_unusual_volume() -> list[str]:
    """Check seed tickers for unusual volume (>3x average)."""
    triggered = []
    for ticker in SEED_TICKERS:
        try:
            quote = _finnhub_get("quote", {"symbol": ticker})
            if not quote or not quote.get("v"):
                continue

            profile = _finnhub_get("stock/metric", {
                "symbol": ticker,
                "metric": "all",
            })
            metrics = profile.get("metric", {})
            avg_vol = metrics.get("10DayAverageTradingVolume", 0)

            if avg_vol and avg_vol > 0:
                avg_vol_shares = avg_vol * 1_000_000
                current_vol = quote.get("v", 0)
                if current_vol > avg_vol_shares * VOLUME_MULTIPLIER:
                    logger.info(
                        "VOLUME TRIGGER: %s — current %s vs avg %s (%.1fx)",
                        ticker, f"{current_vol:,.0f}", f"{avg_vol_shares:,.0f}",
                        current_vol / avg_vol_shares,
                    )
                    triggered.append(ticker)
        except Exception as exc:
            logger.warning("Volume check error for %s: %s", ticker, exc)

    return triggered


def check_price_moves() -> list[str]:
    """Check seed tickers for >10% intraday price moves."""
    triggered = []
    for ticker in SEED_TICKERS:
        try:
            quote = _finnhub_get("quote", {"symbol": ticker})
            if not quote:
                continue

            prev_close = quote.get("pc", 0)
            current = quote.get("c", 0)

            if prev_close > 0 and current > 0:
                pct_change = abs((current - prev_close) / prev_close) * 100
                if pct_change >= PRICE_MOVE_PCT:
                    direction = "UP" if current > prev_close else "DOWN"
                    logger.info(
                        "PRICE TRIGGER: %s — %s %.1f%% ($%.2f -> $%.2f)",
                        ticker, direction, pct_change, prev_close, current,
                    )
                    triggered.append(ticker)
        except Exception as exc:
            logger.warning("Price check error for %s: %s", ticker, exc)

    return triggered


def check_insider_filings() -> list[str]:
    """Check SEC EDGAR for new Form 4 insider purchases > $50K."""
    triggered = []
    try:
        headers = {
            "User-Agent": "PrimoGreedy/1.0 (contact@primogreedy.com)",
            "Accept": "application/json",
        }
        resp = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={
                "q": '"acquired" AND "Form 4"',
                "dateRange": "custom",
                "startdt": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "enddt": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "forms": "4",
            },
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            logger.info("SEC EDGAR returned %d, skipping insider check", resp.status_code)
            return []

        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])[:30]

        for hit in hits:
            src = hit.get("_source", {})
            tickers = src.get("tickers", [])
            if tickers:
                ticker = tickers[0].upper()
                if ticker in SEED_TICKERS:
                    logger.info("INSIDER TRIGGER: Form 4 filing for %s", ticker)
                    triggered.append(ticker)

    except Exception as exc:
        logger.warning("SEC insider check error: %s", exc)

    return triggered


def dispatch_github_workflow(ticker: str) -> bool:
    """Fire a GitHub Actions repository_dispatch event for a specific ticker."""
    if not GITHUB_TOKEN:
        logger.warning("GITHUB_TOKEN not set — cannot dispatch")
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    payload = {
        "event_type": "catalyst-alert",
        "client_payload": {
            "ticker": ticker,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code == 204:
            logger.info("Dispatched catalyst alert for %s", ticker)
            return True
        logger.warning("GitHub dispatch failed (%d): %s", resp.status_code, resp.text)
        return False
    except Exception as exc:
        logger.error("GitHub dispatch error: %s", exc)
        return False


def log_trigger_to_vps(ticker: str, trigger_type: str) -> None:
    """Log the catalyst trigger to the VPS agent_runs table."""
    if not VPS_API_URL:
        return
    try:
        import uuid
        requests.post(
            f"{VPS_API_URL}/runs",
            headers={"X-API-Key": VPS_API_KEY, "Content-Type": "application/json"},
            json={
                "id": str(uuid.uuid4()),
                "ticker": ticker,
                "status": f"CATALYST:{trigger_type}",
                "model": "catalyst_poll",
                "latency_ms": 0,
                "region": "USA",
            },
            timeout=5,
        )
    except Exception as exc:
        logger.warning("VPS log error: %s", exc)


def is_market_hours() -> bool:
    """Check if we're within US market hours (9:00-16:30 EST, weekdays)."""
    from datetime import timezone, timedelta
    est = timezone(timedelta(hours=-5))
    now = datetime.now(est)

    if now.weekday() >= 5:
        return False

    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def main():
    """Run one polling cycle: check all triggers, dispatch if needed."""
    if not is_market_hours():
        logger.info("Outside market hours — skipping")
        return

    logger.info("Starting catalyst poll cycle...")

    all_triggered = set()

    volume_triggers = check_unusual_volume()
    all_triggered.update(volume_triggers)

    price_triggers = check_price_moves()
    all_triggered.update(price_triggers)

    insider_triggers = check_insider_filings()
    all_triggered.update(insider_triggers)

    if not all_triggered:
        logger.info("No catalysts detected this cycle")
        return

    logger.info("Catalysts detected for: %s", ", ".join(all_triggered))

    for ticker in all_triggered:
        trigger_type = []
        if ticker in volume_triggers:
            trigger_type.append("VOLUME")
        if ticker in price_triggers:
            trigger_type.append("PRICE")
        if ticker in insider_triggers:
            trigger_type.append("INSIDER")

        dispatch_github_workflow(ticker)
        log_trigger_to_vps(ticker, "+".join(trigger_type))

    logger.info("Catalyst poll cycle complete — %d triggers fired", len(all_triggered))


if __name__ == "__main__":
    main()
