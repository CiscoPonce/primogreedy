import json
import os
from datetime import datetime

import requests
import yfinance as yf
from src.core.logger import get_logger
from src.core.ticker_utils import normalize_price

logger = get_logger(__name__)

PORTFOLIO_FILE = "paper_portfolio.json"

# VPS Data API (optional — falls back to local JSON if not set)
VPS_API_URL = os.getenv("VPS_API_URL", "").rstrip("/")
VPS_API_KEY = os.getenv("VPS_API_KEY", "")


def _vps_headers() -> dict:
    return {"X-API-Key": VPS_API_KEY, "Content-Type": "application/json"}


def record_paper_trade(
    ticker: str,
    entry_price: float,
    verdict: str,
    source: str,
    structured_verdict: str | None = None,
    position_size: float = 0.0,
) -> None:
    """Save a BUY/STRONG BUY/WATCH recommendation to the paper portfolio.

    When *structured_verdict* is supplied (from ``InvestmentVerdict.verdict``),
    it is used directly, skipping brittle string matching on the full report.

    For US tickers with actionable verdicts, also submits to Alpaca Paper
    Trading (if ``ALPACA_ENABLED=true``).
    """
    if structured_verdict:
        _VALID = {"STRONG BUY", "BUY", "WATCH"}
        trade_type = structured_verdict if structured_verdict in _VALID else None
    else:
        v_upper = verdict.strip().upper()
        trade_type = None
        if "STRONG BUY" in v_upper:
            trade_type = "STRONG BUY"
        elif " BUY" in v_upper or v_upper.startswith("BUY"):
            trade_type = "BUY"
        elif "WATCH" in v_upper:
            trade_type = "WATCH"

    if not trade_type:
        return

    today = datetime.now().strftime("%Y-%m-%d")

    # --- Alpaca broker execution (US equities, BUY/STRONG BUY only) ---
    order_id = None
    fill_price = None
    broker_status = "none"

    try:
        from src.broker.alpaca import calculate_order, submit_order, get_account

        acct = get_account()
        if acct and position_size > 0:
            order_params = calculate_order(
                ticker=ticker,
                verdict=trade_type,
                position_size_pct=position_size,
                account_equity=acct["equity"],
            )
            if order_params:
                result = submit_order(order_params)
                order_id = result.order_id
                fill_price = result.fill_price
                broker_status = result.broker_status
                if result.success:
                    logger.info("Alpaca order filled: %s %d shares @ %s",
                                ticker, result.qty, fill_price or "market")
                else:
                    logger.warning("Alpaca order not filled: %s — %s",
                                   ticker, result.error or broker_status)
    except Exception as exc:
        logger.warning("Alpaca execution skipped for %s: %s", ticker, exc)

    # --- Record to VPS ---
    if VPS_API_URL:
        try:
            resp = requests.post(
                f"{VPS_API_URL}/portfolio",
                headers=_vps_headers(),
                json={
                    "ticker": ticker,
                    "entry_price": entry_price,
                    "date": today,
                    "verdict": trade_type,
                    "source": source,
                    "position_size": position_size,
                    "order_id": order_id,
                    "fill_price": fill_price,
                    "broker_status": broker_status,
                },
                timeout=5,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("status") == "duplicate":
                logger.info("Duplicate trade skipped (VPS): %s on %s", ticker, today)
            else:
                logger.info("Paper trade recorded (VPS): %s %s @ $%.2f", trade_type, ticker, entry_price)
            return
        except Exception as exc:
            logger.warning("VPS record_paper_trade failed, using local fallback: %s", exc)

    # --- Local fallback ---
    try:
        portfolio = []
        if os.path.exists(PORTFOLIO_FILE):
            with open(PORTFOLIO_FILE, "r") as f:
                portfolio = json.load(f)

        for trade in portfolio:
            if trade["ticker"] == ticker and trade["date"] == today:
                logger.info("Duplicate trade skipped: %s on %s", ticker, today)
                return

        trade = {
            "ticker": ticker,
            "entry_price": entry_price,
            "date": today,
            "verdict": trade_type,
            "source": source,
            "position_size": position_size,
            "order_id": order_id,
            "fill_price": fill_price,
            "broker_status": broker_status,
        }

        portfolio.append(trade)

        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(portfolio, f, indent=4)

        logger.info("Paper trade recorded: %s %s @ $%.2f", trade_type, ticker, entry_price)

    except Exception as exc:
        logger.error("Error recording paper trade: %s", exc)


def evaluate_portfolio() -> str:
    """Read the paper portfolio and calculate live performance."""

    # Try VPS first
    if VPS_API_URL:
        try:
            resp = requests.get(
                f"{VPS_API_URL}/portfolio/evaluate",
                headers=_vps_headers(),
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("trades"):
                return "**Paper Portfolio is empty.** The Agent hasn't tracked any stocks yet."

            report = "## PrimoGreedy Agent Track Record\n\n"
            report += "| Ticker | Date Called | Entry Price | Current Price | Return | Verdict |\n"
            report += "|--------|-------------|-------------|---------------|--------|--------|\n"

            for t in data["trades"]:
                if t["gain_pct"] is not None:
                    emoji = "+" if t["gain_pct"] > 0 else ""
                    report += (
                        f"| **{t['ticker']}** | {t['date']} | ${t['entry']:.2f} | "
                        f"${t['current']:.2f} | {emoji}{t['gain_pct']:.2f}% | {t['verdict']} |\n"
                    )
                else:
                    report += (
                        f"| **{t['ticker']}** | {t['date']} | ${t['entry']:.2f} | "
                        f"Error | N/A | {t['verdict']} |\n"
                    )

            report += f"\n### Agent Performance Summary\n"
            report += f"- **Total Calls:** {data['total_calls']}\n"
            report += f"- **Win Rate:** {data['win_rate']}%\n"
            report += f"- **Average Return per Trade:** {data['avg_return']}%\n"

            return report

        except Exception as exc:
            logger.warning("VPS evaluate_portfolio failed, using local fallback: %s", exc)

    # Local fallback (original behavior)
    return _evaluate_local()


def _evaluate_local() -> str:
    """Evaluate portfolio from local JSON file."""
    if not os.path.exists(PORTFOLIO_FILE):
        return "**Paper Portfolio is empty.** The Agent hasn't tracked any stocks yet."

    try:
        with open(PORTFOLIO_FILE, "r") as f:
            portfolio = json.load(f)

        if not portfolio:
            return "**Paper Portfolio is empty.** The Agent hasn't tracked any stocks yet."

        total_roi = 0.0
        winning_trades = 0
        valid_trades = 0

        report = "## PrimoGreedy Agent Track Record\n\n"
        report += "| Ticker | Date Called | Entry Price | Current Price | Return | Verdict |\n"
        report += "|--------|-------------|-------------|---------------|--------|--------|\n"

        for trade in portfolio:
            ticker = trade["ticker"]
            entry = trade["entry_price"]

            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                price = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0
                currency = info.get("currency", "USD")
                price = normalize_price(price, ticker, currency)

                if price > 0 and entry > 0:
                    gain_pct = ((price - entry) / entry) * 100
                    emoji = "+" if gain_pct > 0 else ""
                    if gain_pct > 0:
                        winning_trades += 1
                    total_roi += gain_pct
                    valid_trades += 1

                    report += (
                        f"| **{ticker}** | {trade['date']} | ${entry:.2f} | "
                        f"${price:.2f} | {emoji}{gain_pct:.2f}% | {trade['verdict']} |\n"
                    )
                else:
                    report += (
                        f"| **{ticker}** | {trade['date']} | ${entry:.2f} | "
                        f"Error | N/A | {trade['verdict']} |\n"
                    )
            except Exception as exc:
                logger.warning("Price fetch failed for %s: %s", ticker, exc)
                report += (
                    f"| **{ticker}** | {trade['date']} | ${entry:.2f} | "
                    f"Error | N/A | {trade['verdict']} |\n"
                )

        if valid_trades > 0:
            avg_roi = total_roi / valid_trades
            win_rate = (winning_trades / valid_trades) * 100
        else:
            avg_roi = 0
            win_rate = 0

        report += f"\n### Agent Performance Summary\n"
        report += f"- **Total Calls:** {len(portfolio)}\n"
        report += f"- **Win Rate:** {win_rate:.1f}%\n"
        report += f"- **Average Return per Trade:** {avg_roi:.2f}%\n"

        return report

    except Exception as exc:
        logger.error("Portfolio evaluation error: %s", exc)
        return f"Error reading portfolio: {exc}"
