"""Re-evaluate existing US tickers from VPS portfolio.

Designed to run as a GitHub Action (or locally via CLI).
Fetches tickers from the VPS DuckDB, runs each through the whale_hunter
analyst pipeline, and updates verdicts + Alpaca orders in VPS.

Usage:
    PYTHONPATH=. python src/reeval_portfolio.py              # all US tickers
    PYTHONPATH=. python src/reeval_portfolio.py --limit 3    # first 3 only
"""

import os
import re
import sys
import time
import warnings
from datetime import datetime
from typing import Optional

import requests
import yfinance as yf

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

from src.core.logger import get_logger
from src.core.ticker_utils import normalize_price
from src.core.online_eval import log_online_feedback, tag_for_review, get_current_run_id
from src.llm import get_structured_llm, invoke_with_fallback, MODEL_CHAIN
from src.finance_tools import (
    check_financial_health,
    get_insider_sentiment,
    get_company_news,
    get_basic_financials,
)
from src.portfolio_tracker import record_paper_trade
from src.discovery.insider_feed import get_insider_buys
from src.email_utils import send_email_report

logger = get_logger("reeval_portfolio")

# --- Config ---
VPS_API_URL = os.getenv("VPS_API_URL", "").rstrip("/")
VPS_API_KEY = os.getenv("VPS_API_KEY", "")
MAX_PRICE = 30.0
MIN_CAP = 5_000_000
MAX_CAP = 500_000_000

# Tickers to always skip (test entries, large-caps used for smoke tests)
SKIP_TICKERS = {
    "TEST1", "PYTEST1", "PYTH", "GHACT",
    "AAPL", "TSLA", "MSFT", "GOOG", "NVDA", "AMZN", "META",
}
_TEST_RE = re.compile(r"^(TEST|PYTEST|TST|T\d{3,})", re.IGNORECASE)


def _vps_headers():
    return {"X-API-Key": VPS_API_KEY, "Content-Type": "application/json"}


def fetch_us_tickers() -> list[dict]:
    """Pull US-only tickers from VPS portfolio, deduplicated by latest date."""
    resp = requests.get(f"{VPS_API_URL}/portfolio", headers=_vps_headers(), timeout=10)
    resp.raise_for_status()

    seen = {}
    for trade in resp.json():
        ticker = trade["ticker"]
        if ("." not in ticker
                and ticker not in SKIP_TICKERS
                and not _TEST_RE.match(ticker)):
            if ticker not in seen or trade["date"] > seen[ticker]["date"]:
                seen[ticker] = trade
    return list(seen.values())


def analyse_ticker(ticker: str, company_name: str, info: dict) -> Optional[dict]:
    """Run the Senior Broker analyst pipeline on a single ticker.

    Returns dict with verdict, report, position_size or None on failure.
    """
    price = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0
    eps = info.get("trailingEps", 0) or 0
    book_value = info.get("bookValue", 0) or 0
    ebitda = info.get("ebitda", 0) or 0
    currency = info.get("currency", "USD")

    price = normalize_price(price, ticker, currency)

    if eps > 0 and book_value > 0:
        strategy = "GRAHAM CLASSIC"
        valuation = (22.5 * eps * book_value) ** 0.5
        thesis = f"Profitable. Graham Value ${valuation:.2f} vs Price ${price:.2f}."
    else:
        strategy = "DEEP VALUE ASSET PLAY"
        ratio = price / book_value if book_value > 0 else 0
        thesis = f"Unprofitable. Trading at {ratio:.2f}x Book Value."

    # --- Gather context ---
    from src.core.search import brave_search
    news = brave_search(f"{ticker} stock analysis catalysts")

    # SEC EDGAR
    sec_context = ""
    try:
        from src.sec_edgar import get_sec_filings
        sec_context = get_sec_filings.invoke({"ticker": ticker})
    except Exception as exc:
        logger.warning("SEC EDGAR failed for %s: %s", ticker, exc)

    # Finnhub + Insider
    deep_fundamentals = ""
    context = ""
    try:
        context += get_insider_sentiment.invoke({"ticker": ticker}) + "\n"
        context += get_company_news.invoke({"ticker": ticker}) + "\n"
        context += get_basic_financials.invoke({"ticker": ticker}) + "\n"
    except Exception as exc:
        logger.warning("Finnhub error for %s: %s", ticker, exc)

    insider = get_insider_buys(ticker)
    context += f"\nInsider Sentiment (6mo): {insider['sentiment']} | MSPR: {insider['mspr']} | Net Shares: {insider['change']}\n"
    deep_fundamentals = f"DEEP FUNDAMENTALS (FINNHUB + INSIDER FEED):\n{context}"

    # --- Debate or single-LLM path ---
    from src.agents.debate import is_debate_enabled, run_debate
    from src.models.kelly import get_kelly_stats, calculate_position_size

    if is_debate_enabled():
        logger.info("Running debate for %s...", ticker)
        try:
            debate_result = run_debate(
                ticker=ticker,
                company_name=company_name,
                financial_data_summary=str(info)[:2000],
                deep_fundamentals=deep_fundamentals,
                sec_context=sec_context,
                strategy=strategy,
                price=price, eps=eps, book_value=book_value, ebitda=ebitda,
            )
            result = debate_result["_structured_result"]
            stats = get_kelly_stats()
            result.position_size = calculate_position_size(stats, result.verdict)
            result.kelly_win_rate = stats.win_rate
            result.kelly_total_trades = stats.total_trades
            report = result.to_report()
            return {
                "verdict": result.verdict,
                "report": report,
                "position_size": result.position_size,
                "price": price,
            }
        except Exception as exc:
            logger.warning("Debate failed for %s, falling back: %s", ticker, exc)

    # --- Single-LLM structured output ---
    from src.prompts.senior_broker import get_analyst_prompt
    template = get_analyst_prompt()
    prompt = template.format(
        company_name=company_name, ticker=ticker,
        price=price, eps=eps, book_value=book_value, ebitda=ebitda,
        thesis=thesis, strategy=strategy,
        deep_fundamentals=deep_fundamentals, sec_context=sec_context,
    )

    try:
        from src.models.verdict import InvestmentVerdict
        structured_llm = get_structured_llm().with_structured_output(InvestmentVerdict)
        result = structured_llm.invoke(prompt)
        stats = get_kelly_stats()
        result.position_size = calculate_position_size(stats, result.verdict)
        result.kelly_win_rate = stats.win_rate
        result.kelly_total_trades = stats.total_trades
        report = result.to_report()
        return {
            "verdict": result.verdict,
            "report": report,
            "position_size": result.position_size,
            "price": price,
        }
    except Exception as exc:
        logger.warning("Structured output failed for %s, fallback to plain LLM: %s", ticker, exc)

    # --- Plain LLM fallback ---
    try:
        report = invoke_with_fallback(prompt)
        stats = get_kelly_stats()
        v_upper = report.upper()
        verdict = "AVOID"
        if "STRONG BUY" in v_upper:
            verdict = "STRONG BUY"
        elif "BUY" in v_upper:
            verdict = "BUY"
        elif "WATCH" in v_upper:
            verdict = "WATCH"
        pos = calculate_position_size(stats, verdict)
        return {"verdict": verdict, "report": report, "position_size": pos, "price": price}
    except Exception as exc:
        logger.error("All LLM paths failed for %s: %s", ticker, exc)
        return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Re-evaluate US tickers from VPS")
    parser.add_argument("--limit", type=int, default=0, help="Max tickers (0 = all)")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("PrimoGreedy — Portfolio Re-Evaluation")
    logger.info("Alpaca: %s | Debate: %s", os.getenv("ALPACA_ENABLED", "false"),
                os.getenv("USE_DEBATE", "false"))
    logger.info("=" * 60)

    trades = fetch_us_tickers()
    if not trades:
        logger.info("No US tickers found in VPS portfolio.")
        return

    if args.limit > 0:
        trades = trades[:args.limit]

    logger.info("Re-evaluating %d US tickers", len(trades))

    results = []

    for i, trade in enumerate(trades, 1):
        ticker = trade["ticker"]
        old_verdict = trade["verdict"]
        logger.info("[%d/%d] %s (old: %s, entry: $%.2f)",
                    i, len(trades), ticker, old_verdict, trade["entry_price"])

        # Gatekeeper: quick check if ticker still qualifies
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            mkt_cap = info.get("marketCap", 0) or 0
            price = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0
            name = info.get("shortName", ticker)

            if not (MIN_CAP < mkt_cap < MAX_CAP):
                logger.info("%s skipped — cap $%s out of range", ticker, f"{mkt_cap:,.0f}")
                results.append({"ticker": ticker, "old": old_verdict, "new": "SKIP", "reason": "cap out of range"})
                continue

            health = check_financial_health(ticker, info)
            if health["status"] == "FAIL":
                logger.info("%s skipped — %s", ticker, health["reason"])
                results.append({"ticker": ticker, "old": old_verdict, "new": "SKIP", "reason": health["reason"]})
                continue
        except Exception as exc:
            logger.error("yFinance error for %s: %s", ticker, exc)
            results.append({"ticker": ticker, "old": old_verdict, "new": "ERROR", "reason": str(exc)})
            continue

        # Analyst
        analysis = analyse_ticker(ticker, name, info)
        if not analysis:
            results.append({"ticker": ticker, "old": old_verdict, "new": "ERROR", "reason": "LLM failed"})
            continue

        new_verdict = analysis["verdict"]
        changed = new_verdict != old_verdict

        # Record the re-evaluated trade (this handles Alpaca + VPS automatically)
        record_paper_trade(
            ticker, analysis["price"], analysis["report"],
            source="reeval_cron",
            structured_verdict=new_verdict,
            position_size=analysis["position_size"],
        )

        # Online eval feedback
        _run_id = get_current_run_id()
        log_online_feedback(analysis["report"], ticker, run_id=_run_id)
        tag_for_review(analysis["report"], ticker, run_id=_run_id)

        icon = "🔄" if changed else "✅"
        logger.info("%s %s: %s → %s (pos: %.1f%%)",
                    icon, ticker, old_verdict, new_verdict, analysis["position_size"])

        results.append({
            "ticker": ticker, "old": old_verdict, "new": new_verdict,
            "changed": changed, "position_size": analysis["position_size"],
        })

        if i < len(trades):
            time.sleep(2)

    # --- Summary ---
    logger.info("=" * 60)
    logger.info("RE-EVALUATION COMPLETE")
    logger.info("=" * 60)

    changed_count = sum(1 for r in results if r.get("changed"))
    for r in results:
        status = r.get("new", "?")
        logger.info("  %s: %s → %s", r["ticker"], r.get("old", "?"), status)

    logger.info("Total: %d | Changed: %d | Same: %d",
                len(results), changed_count, len(results) - changed_count)

    # --- Email summary ---
    email_body = "<h1>Portfolio Re-Evaluation Results</h1><table>"
    email_body += "<tr><th>Ticker</th><th>Old</th><th>New</th><th>Status</th></tr>"
    for r in results:
        color = "#34d399" if not r.get("changed") else "#fbbf24"
        email_body += f"<tr><td>{r['ticker']}</td><td>{r.get('old','?')}</td>"
        email_body += f"<td style='color:{color}'>{r.get('new','?')}</td>"
        email_body += f"<td>{'CHANGED' if r.get('changed') else 'SAME'}</td></tr>"
    email_body += "</table>"

    team = [
        {"name": "Cisco", "email": os.getenv("EMAIL_CISCO"), "key": os.getenv("RESEND_API_KEY_CISCO")},
    ]
    for member in team:
        if member["email"] and member["key"]:
            try:
                send_email_report("Portfolio Re-Eval Complete", email_body, member["email"], member["key"])
            except Exception as exc:
                logger.warning("Email to %s failed: %s", member["name"], exc)


if __name__ == "__main__":
    main()
