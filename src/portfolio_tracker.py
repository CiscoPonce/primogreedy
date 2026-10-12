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


# ---------------------------------------------------------------------------
# 3-layer framework: submit analyses -> stage on watchlist -> review gate
# (proposal.md). Analyses NEVER execute orders. A trade is only recorded after
# an explicit accept (human via dashboard/Chainlit, or API).
# ---------------------------------------------------------------------------

ANALYSIS_FILE = "analyses.json"

# Fields we lift from a structured InvestmentVerdict into the analysis payload
_FRAMEWORK_FIELDS = [
    "business_summary", "financial_evolution", "cash_flow", "debt_liquidity",
    "dilution", "valuation_method", "valuation_bear", "valuation_base",
    "valuation_bull", "valuation_range_low", "valuation_range_high",
    "margin_of_safety", "risks", "invalidation_facts", "macro_factors",
    "portfolio_fit", "sources", "data_quality", "review_trigger", "next_review_at",
]


def _pick(obj, name):
    """Read a field from a pydantic model or dict, tolerating missing attrs."""
    if obj is None:
        return None
    try:
        value = getattr(obj, name)
    except AttributeError:
        value = obj.get(name) if isinstance(obj, dict) else None
    if value in (None, "", [], {}):
        return None
    return value


def _framework_payload(structured=None, proposed_action=None) -> dict:
    """Extract the 3-layer framework fields from a structured verdict."""
    payload = {name: _pick(structured, name) for name in _FRAMEWORK_FIELDS}
    if proposed_action:
        payload["proposed_action"] = proposed_action
    elif structured is not None:
        payload["proposed_action"] = _pick(structured, "proposed_action")
    if isinstance(payload.get("sources"), list):
        payload["sources"] = [
            s if isinstance(s, dict) else {"type": "self", "url": None, "value": str(s)}
            for s in payload["sources"]
        ]
    return payload


def submit_analysis(
    ticker: str,
    price: float,
    source: str,
    verdict: str | None = None,
    report_text: str | None = None,
    position_size: float = 0.0,
    structured=None,
    proposed_action: str | None = None,
) -> dict | None:
    """Submit a framework-compliant analysis and stage the ticker for review.

    Replaces automatic paper-trading: the analysis lands on the watchlist /
    review queue, and a paper trade is only recorded after an explicit accept.
    Never executes a broker order by itself.
    """
    payload = {
        "ticker": ticker,
        "stage": None,                       # VPS derives stage from verdict
        "verdict": verdict,
        "source": source,
        "price": float(price) if price else None,
        "currency": "USD",
        "report_text": report_text,
        "position_size": float(position_size or 0),
        **_framework_payload(structured, proposed_action=proposed_action),
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    if VPS_API_URL:
        try:
            resp = requests.post(
                f"{VPS_API_URL}/analyses", headers=_vps_headers(), json=payload, timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("Analysis submitted (VPS): %s stage=%s",
                        ticker, data.get("stage", "?"))
            return data
        except Exception as exc:
            logger.warning("submit_analysis failed (VPS), using local fallback: %s", exc)

    # Local fallback (keeps behavior when VPS is unreachable)
    try:
        records = []
        if os.path.exists(ANALYSIS_FILE):
            with open(ANALYSIS_FILE, "r") as f:
                records = json.load(f)
        records.append({**payload, "analyzed_at": datetime.now().isoformat()})
        with open(ANALYSIS_FILE, "w") as f:
            json.dump(records, f, indent=2)
        logger.info("Analysis submitted (local fallback): %s", ticker)
        return {"status": "ok", "ticker": ticker}
    except Exception as exc:
        logger.error("submit_analysis local fallback failed: %s", exc)
        return None


def get_pending_reviews() -> list:
    """Active watchlist items awaiting an accept/dismiss decision."""
    if not VPS_API_URL:
        return []
    try:
        resp = requests.get(
            f"{VPS_API_URL}/portfolio/reviews", headers=_vps_headers(), timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("get_pending_reviews failed: %s", exc)
        return []


def accept_decision(ticker: str, action: str | None = None,
                    position_size: float | None = None,
                    entry_price: float | None = None) -> dict | None:
    """Accept a staged analysis — the VPS writes the paper trade (no orders)."""
    if not VPS_API_URL:
        logger.warning("accept_decision skipped: VPS_API_URL not set")
        return None
    body = {"action": action, "position_size": position_size, "entry_price": entry_price}
    body = {k: v for k, v in body.items() if v is not None}
    resp = requests.post(
        f"{VPS_API_URL}/watchlist/{ticker}/accept",
        headers=_vps_headers(), json=body, timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def dismiss_decision(ticker: str) -> dict | None:
    """Dismiss a staged analysis without recording any trade."""
    if not VPS_API_URL:
        logger.warning("dismiss_decision skipped: VPS_API_URL not set")
        return None
    resp = requests.post(
        f"{VPS_API_URL}/watchlist/{ticker}/dismiss",
        headers=_vps_headers(), timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


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
