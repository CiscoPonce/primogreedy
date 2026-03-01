"""Alternative data signals: SEC insider buys, Finnhub insider sentiment (Proposal H).

Provides direct feeds for insider-buying activity which is one of the
strongest known alpha signals in micro-cap investing.
"""

import os
from datetime import datetime, timedelta
import requests
from src.core.logger import get_logger

logger = get_logger(__name__)


def get_insider_buys(ticker: str) -> dict:
    """Fetch insider buy/sell summary from Finnhub for a US stock.

    Returns:
        dict with keys: sentiment ("Bullish"/"Bearish"/"Neutral"),
        mspr (Monthly Share Purchase Ratio), change (net shares),
        raw_data (list of monthly records).
    """
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        logger.warning("FINNHUB_API_KEY not set")
        return {"sentiment": "Unknown", "mspr": 0, "change": 0, "raw_data": []}

    if "." in ticker:
        return {"sentiment": "N/A (non-US)", "mspr": 0, "change": 0, "raw_data": []}

    url = "https://finnhub.io/api/v1/stock/insider-sentiment"
    params = {
        "symbol": ticker,
        "from": (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d"),
        "to": datetime.now().strftime("%Y-%m-%d"),
        "token": api_key,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        records = data.get("data", [])
        if not records:
            return {"sentiment": "No Data", "mspr": 0, "change": 0, "raw_data": []}

        total_mspr = sum(r.get("mspr", 0) for r in records)
        total_change = sum(r.get("change", 0) for r in records)

        if total_mspr > 0:
            sentiment = "Bullish (Net Insider Buying)"
        elif total_mspr < 0:
            sentiment = "Bearish (Net Insider Selling)"
        else:
            sentiment = "Neutral"

        return {
            "sentiment": sentiment,
            "mspr": round(total_mspr, 4),
            "change": total_change,
            "raw_data": records[-3:],  # last 3 months
        }

    except requests.exceptions.RequestException as exc:
        logger.error("Finnhub insider error for %s: %s", ticker, exc)
        return {"sentiment": "Error", "mspr": 0, "change": 0, "raw_data": []}


def get_sec_form4_feed(max_items: int = 20) -> list[dict]:
    """Fetch recent SEC Form 4 (insider transaction) filings from EDGAR RSS.

    This taps directly into the SEC's public EDGAR full-text search for
    Form 4 filings, providing a near-real-time feed of insider buys.

    Returns:
        List of dicts with: ticker, filer, transaction_type, shares,
        price, filing_date, url.
    """
    try:
        url = "https://efts.sec.gov/LATEST/search-index"
        params = {
            "q": '"acquired" AND "Form 4"',
            "dateRange": "custom",
            "startdt": (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),
            "enddt": datetime.now().strftime("%Y-%m-%d"),
            "forms": "4",
        }
        headers = {
            "User-Agent": "PrimoGreedy/1.0 (contact@primogreedy.com)",
            "Accept": "application/json",
        }

        resp = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params=params,
            headers=headers,
            timeout=15,
        )

        if resp.status_code != 200:
            logger.info(
                "SEC EDGAR feed returned %d – falling back to Finnhub",
                resp.status_code,
            )
            return _fallback_finnhub_insider_feed(max_items)

        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])[:max_items]

        results = []
        for hit in hits:
            src = hit.get("_source", {})
            results.append({
                "ticker": src.get("tickers", [""])[0] if src.get("tickers") else "",
                "filer": src.get("display_names", [""])[0] if src.get("display_names") else "",
                "filing_date": src.get("file_date", ""),
                "url": f"https://www.sec.gov/Archives/edgar/data/{src.get('file_num', '')}",
                "form_type": src.get("form_type", "4"),
            })

        logger.info("SEC Form 4 feed returned %d filings", len(results))
        return results

    except requests.exceptions.RequestException as exc:
        logger.warning("SEC EDGAR feed error: %s – using Finnhub fallback", exc)
        return _fallback_finnhub_insider_feed(max_items)


def _fallback_finnhub_insider_feed(max_items: int = 20) -> list[dict]:
    """Fallback: use Finnhub's insider-transactions endpoint."""
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        return []

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/stock/insider-transactions",
            params={
                "symbol": "",  # blank = latest across all tickers
                "token": api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])[:max_items]

        results = []
        for txn in data:
            if txn.get("transactionCode") in ("P", "A"):  # Purchase or Award
                results.append({
                    "ticker": txn.get("symbol", ""),
                    "filer": txn.get("name", ""),
                    "transaction_type": "BUY" if txn.get("transactionCode") == "P" else "AWARD",
                    "shares": txn.get("share", 0),
                    "price": txn.get("transactionPrice", 0),
                    "filing_date": txn.get("filingDate", ""),
                    "url": "",
                })

        logger.info("Finnhub insider fallback returned %d transactions", len(results))
        return results

    except requests.exceptions.RequestException as exc:
        logger.error("Finnhub insider-transactions error: %s", exc)
        return []
