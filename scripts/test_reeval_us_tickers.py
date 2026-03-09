"""Re-evaluate US tickers from VPS portfolio through the full agent pipeline.

For each US ticker with a previous BUY/STRONG BUY verdict stored on the VPS:
  1. Run the full LangGraph pipeline (gatekeeper → analyst with debate/Kelly).
  2. Compare the new verdict against the old one.
  3. If the verdict changed, update the VPS DuckDB via the data API.
  4. If the new verdict is still BUY/STRONG BUY, route through Alpaca paper trading.

Usage:
    python -m scripts.test_reeval_us_tickers          # dry-run (no Alpaca orders)
    ALPACA_ENABLED=true python -m scripts.test_reeval_us_tickers  # live paper orders
"""

import os
import sys
import json
import time
import warnings
import requests
from datetime import datetime
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

from src.core.logger import get_logger
from src.agent import app as agent_app  # The compiled LangGraph

logger = get_logger("reeval_test")

# VPS config
VPS_API_URL = os.getenv("VPS_API_URL", "").rstrip("/")
VPS_API_KEY = os.getenv("VPS_API_KEY", "")

def _vps_headers():
    return {"X-API-Key": VPS_API_KEY, "Content-Type": "application/json"}


def fetch_us_tickers_from_vps() -> list[dict]:
    """Get all US tickers from VPS portfolio (no dot in ticker = US)."""
    resp = requests.get(f"{VPS_API_URL}/portfolio", headers=_vps_headers(), timeout=10)
    resp.raise_for_status()
    portfolio = resp.json()

    # Filter US-only tickers (no dot suffix), deduplicate by keeping latest
    # Exclude test entries and large-cap tickers used for testing
    EXCLUDE = {
        "TEST1", "PYTEST1", "PYTH", "GHACT", "TEST.VPS",
        # Large-cap tickers used for VPS smoke tests, not real micro-cap finds
        "AAPL", "TSLA", "MSFT", "GOOG", "NVDA", "AMZN", "META",
    }
    import re
    _TEST_RE = re.compile(r"^(TEST|PYTEST|TST|T\d{3,})", re.IGNORECASE)

    seen = {}
    for trade in portfolio:
        ticker = trade["ticker"]
        if ("." not in ticker
                and ticker not in EXCLUDE
                and not _TEST_RE.match(ticker)):
            # Keep the most recent entry for each ticker
            if ticker not in seen or trade["date"] > seen[ticker]["date"]:
                seen[ticker] = trade

    return list(seen.values())


def extract_verdict_from_report(report: str) -> str:
    """Extract the verdict keyword from a full report text."""
    upper = report.upper()
    if "STRONG BUY" in upper:
        return "STRONG BUY"
    if "BUY" in upper:
        return "BUY"
    if "WATCH" in upper:
        return "WATCH"
    return "AVOID"


def run_agent_for_ticker(ticker: str) -> dict:
    """Run the full agent pipeline for a single ticker.

    Uses the same LangGraph app as the Chainlit UI.
    Returns the final state dict.
    """
    config = {
        "configurable": {"thread_id": f"reeval-{ticker.lower()}-{int(time.time())}"},
        "recursion_limit": 30,
    }
    # Set the ticker as manual search and region as USA
    initial = {"ticker": ticker, "region": "USA"}

    result = agent_app.invoke(initial, config)
    return result


def update_vps_portfolio(ticker: str, entry_price: float, verdict: str,
                         position_size: float = 0.0,
                         order_id: str = None,
                         fill_price: float = None,
                         broker_status: str = "none") -> bool:
    """Record the re-evaluated trade to VPS."""
    today = datetime.now().strftime("%Y-%m-%d")
    payload = {
        "ticker": ticker,
        "entry_price": entry_price,
        "date": today,
        "verdict": verdict,
        "source": "reeval_test",
        "position_size": position_size,
        "order_id": order_id,
        "fill_price": fill_price,
        "broker_status": broker_status,
    }
    try:
        resp = requests.post(
            f"{VPS_API_URL}/portfolio",
            headers=_vps_headers(),
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") == "duplicate":
            logger.info("[VPS] Duplicate skipped for %s on %s", ticker, today)
            return False
        logger.info("[VPS] Recorded: %s %s @ $%.2f", verdict, ticker, entry_price)
        return True
    except Exception as exc:
        logger.error("[VPS] Failed to record %s: %s", ticker, exc)
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=3,
                        help="Max tickers to evaluate (default 3)")
    args = parser.parse_args()

    print("=" * 70)
    print("PrimoGreedy — US Ticker Re-Evaluation Test")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Alpaca Enabled: {os.getenv('ALPACA_ENABLED', 'false')}")
    print(f"VPS API: {VPS_API_URL}")
    print(f"Ticker Limit: {args.limit}")
    print("=" * 70)

    # 1. Fetch US tickers from VPS
    print("\n📡 Fetching US tickers from VPS portfolio...")
    us_trades = fetch_us_tickers_from_vps()

    if not us_trades:
        print("❌ No US tickers found in VPS portfolio.")
        return

    # Apply limit
    if args.limit and len(us_trades) > args.limit:
        print(f"\n⚠️  Limiting to {args.limit} of {len(us_trades)} tickers (use --limit N to change)")
        us_trades = us_trades[:args.limit]

    print(f"\n✅ Re-evaluating {len(us_trades)} US tickers:\n")
    print(f"{'Ticker':<10} {'Old Verdict':<15} {'Entry Price':<12} {'Date':<12}")
    print("-" * 50)
    for t in us_trades:
        print(f"{t['ticker']:<10} {t['verdict']:<15} ${t['entry_price']:<11.2f} {t['date']:<12}")

    # 2. Run each ticker through the agent
    results = []
    for i, trade in enumerate(us_trades, 1):
        ticker = trade["ticker"]
        old_verdict = trade["verdict"]

        print(f"\n{'='*70}")
        print(f"[{i}/{len(us_trades)}] Re-evaluating {ticker} (old verdict: {old_verdict})")
        print(f"{'='*70}")

        try:
            state = run_agent_for_ticker(ticker)
            report = state.get("final_verdict", state.get("final_report", ""))
            new_verdict = extract_verdict_from_report(report)
            changed = new_verdict != old_verdict

            result = {
                "ticker": ticker,
                "old_verdict": old_verdict,
                "new_verdict": new_verdict,
                "changed": changed,
                "report_preview": report[:300] if report else "No report",
            }
            results.append(result)

            status_icon = "🔄" if changed else "✅"
            print(f"\n{status_icon} {ticker}: {old_verdict} → {new_verdict}"
                  f"{' [CHANGED]' if changed else ' [SAME]'}")

            # 3. If verdict changed, update VPS
            if changed:
                print(f"   📝 Updating VPS with new verdict...")
                # Get current price from state
                fin_data = state.get("financial_data", {})
                current_price = fin_data.get("currentPrice", 0) or fin_data.get("regularMarketPrice", 0) or trade["entry_price"]
                update_vps_portfolio(ticker, current_price, new_verdict)

            # Print report preview
            print(f"\n   📋 Report Preview:")
            for line in report[:500].split("\n"):
                print(f"      {line}")
            if len(report) > 500:
                print(f"      ... ({len(report) - 500} more chars)")

        except Exception as exc:
            logger.error("Failed to evaluate %s: %s", ticker, exc, exc_info=True)
            results.append({
                "ticker": ticker,
                "old_verdict": old_verdict,
                "new_verdict": "ERROR",
                "changed": True,
                "report_preview": str(exc),
            })
            print(f"\n❌ {ticker}: ERROR — {exc}")

        # Small pause between tickers to avoid rate limits
        if i < len(us_trades):
            time.sleep(3)

    # 4. Summary
    print(f"\n{'='*70}")
    print("📊 RE-EVALUATION SUMMARY")
    print(f"{'='*70}")
    print(f"\n{'Ticker':<10} {'Old':<15} {'New':<15} {'Status':<10}")
    print("-" * 50)
    for r in results:
        status = "CHANGED" if r["changed"] else "SAME"
        icon = "🔄" if r["changed"] else "✅"
        print(f"{r['ticker']:<10} {r['old_verdict']:<15} {r['new_verdict']:<15} {icon} {status}")

    changed_count = sum(1 for r in results if r["changed"])
    print(f"\n📈 Total: {len(results)} tickers | {changed_count} changed | {len(results) - changed_count} same")
    print(f"⏱️  Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
