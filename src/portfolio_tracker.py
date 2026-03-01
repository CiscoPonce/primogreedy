import json
import os
from datetime import datetime
import yfinance as yf
from src.core.logger import get_logger
from src.core.ticker_utils import normalize_price

logger = get_logger(__name__)

PORTFOLIO_FILE = "paper_portfolio.json"


def record_paper_trade(ticker: str, entry_price: float, verdict: str, source: str) -> None:
    """Save a BUY/STRONG BUY/WATCH recommendation to the paper portfolio."""
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

    try:
        portfolio = []
        if os.path.exists(PORTFOLIO_FILE):
            with open(PORTFOLIO_FILE, "r") as f:
                portfolio = json.load(f)

        today = datetime.now().strftime("%Y-%m-%d")
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
        }

        portfolio.append(trade)

        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(portfolio, f, indent=4)

        logger.info("Paper trade recorded: %s %s @ $%.2f", trade_type, ticker, entry_price)

    except Exception as exc:
        logger.error("Error recording paper trade: %s", exc)


def evaluate_portfolio() -> str:
    """Read the paper portfolio and calculate live performance."""
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
        report += "|--------|-------------|-------------|---------------|--------|---------|\n"

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
