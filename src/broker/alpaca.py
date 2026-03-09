"""Alpaca Paper Trading integration for PrimoGreedy.

Provides order routing, submission, and status tracking via the Alpaca
Markets Paper Trading API.  Only fires for US equities with actionable
BUY / STRONG BUY verdicts.

Config (env vars):
    ALPACA_API_KEY      — Alpaca paper trading API key
    ALPACA_SECRET_KEY   — Alpaca paper trading secret key
    ALPACA_ENABLED      — set to "true" to enable live order submission
"""

import os
import time
import math
from dataclasses import dataclass
from typing import Optional

import yfinance as yf

from src.core.logger import get_logger

logger = get_logger(__name__)

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
MAX_POSITION_PCT = 25.0
MIN_ORDER_DOLLARS = 1.0
ACTIONABLE_VERDICTS = {"BUY", "STRONG BUY"}


def _is_enabled() -> bool:
    return os.getenv("ALPACA_ENABLED", "").lower() in ("true", "1", "yes")


def _get_api():
    """Lazy-import and return a configured Alpaca REST client."""
    try:
        from alpaca_trade_api import REST
    except ImportError:
        logger.error("alpaca-trade-api not installed — pip install alpaca-trade-api")
        return None

    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        logger.warning("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
        return None

    return REST(api_key, secret_key, base_url=PAPER_BASE_URL)


@dataclass
class OrderResult:
    """Result of an Alpaca order submission."""
    success: bool
    order_id: Optional[str] = None
    fill_price: Optional[float] = None
    broker_status: str = "none"
    error: Optional[str] = None
    qty: int = 0
    symbol: str = ""


def get_account() -> Optional[dict]:
    """Return account summary (equity, buying power, etc.)."""
    api = _get_api()
    if not api:
        return None
    try:
        acct = api.get_account()
        return {
            "equity": float(acct.equity),
            "buying_power": float(acct.buying_power),
            "cash": float(acct.cash),
            "status": acct.status,
        }
    except Exception as exc:
        logger.error("Alpaca account error: %s", exc)
        return None


def get_position(symbol: str) -> Optional[dict]:
    """Return current position for a symbol, or None."""
    api = _get_api()
    if not api:
        return None
    try:
        pos = api.get_position(symbol)
        return {
            "symbol": pos.symbol,
            "qty": int(pos.qty),
            "avg_entry_price": float(pos.avg_entry_price),
            "market_value": float(pos.market_value),
            "unrealized_pl": float(pos.unrealized_pl),
        }
    except Exception:
        return None


def calculate_order(
    ticker: str,
    verdict: str,
    position_size_pct: float,
    account_equity: float,
) -> Optional[dict]:
    """Convert a verdict + Kelly sizing into concrete order parameters.

    Returns None if the order should not be placed (non-US ticker,
    non-actionable verdict, or sizing too small).
    """
    if "." in ticker:
        logger.info("Skipping Alpaca for non-US ticker %s", ticker)
        return None

    verdict_upper = verdict.upper().strip()
    if verdict_upper not in ACTIONABLE_VERDICTS:
        logger.info("Skipping Alpaca — verdict '%s' is not actionable", verdict)
        return None

    capped_pct = min(position_size_pct, MAX_POSITION_PCT)
    if capped_pct <= 0:
        logger.info("Skipping Alpaca — position size is 0%%")
        return None

    dollar_amount = account_equity * (capped_pct / 100.0)
    if dollar_amount < MIN_ORDER_DOLLARS:
        logger.info("Skipping Alpaca — dollar amount $%.2f below minimum", dollar_amount)
        return None

    try:
        stock = yf.Ticker(ticker)
        current_price = stock.info.get("currentPrice") or stock.info.get("regularMarketPrice") or 0
    except Exception as exc:
        logger.warning("Could not fetch price for %s: %s", ticker, exc)
        current_price = 0

    if current_price <= 0:
        logger.warning("Invalid price for %s, cannot calculate qty", ticker)
        return None

    qty = max(1, math.floor(dollar_amount / current_price))

    return {
        "symbol": ticker,
        "qty": qty,
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "dollar_amount": round(dollar_amount, 2),
        "current_price": round(current_price, 2),
    }


def submit_order(order_params: dict) -> OrderResult:
    """Submit a market order to Alpaca Paper Trading.

    Polls for fill status up to 30 seconds.
    """
    if not _is_enabled():
        logger.info("Alpaca disabled — dry-run order: %s", order_params)
        return OrderResult(
            success=True,
            order_id="dry-run",
            broker_status="simulated",
            qty=order_params.get("qty", 0),
            symbol=order_params.get("symbol", ""),
        )

    api = _get_api()
    if not api:
        return OrderResult(success=False, error="Alpaca API not configured")

    try:
        order = api.submit_order(
            symbol=order_params["symbol"],
            qty=order_params["qty"],
            side=order_params["side"],
            type=order_params["type"],
            time_in_force=order_params["time_in_force"],
        )
        order_id = order.id
        logger.info("Order submitted: %s (id=%s)", order_params["symbol"], order_id)

        fill_price = None
        status = order.status
        for _ in range(6):
            time.sleep(5)
            order = api.get_order(order_id)
            status = order.status
            if status == "filled":
                fill_price = float(order.filled_avg_price) if order.filled_avg_price else None
                break
            if status in ("canceled", "expired", "rejected"):
                break

        return OrderResult(
            success=(status == "filled"),
            order_id=order_id,
            fill_price=fill_price,
            broker_status=status,
            qty=int(order_params["qty"]),
            symbol=order_params["symbol"],
        )

    except Exception as exc:
        logger.error("Alpaca order submission failed: %s", exc)
        return OrderResult(success=False, error=str(exc), symbol=order_params.get("symbol", ""))


def get_order_status(order_id: str) -> Optional[dict]:
    """Check the status of an existing order."""
    api = _get_api()
    if not api:
        return None
    try:
        order = api.get_order(order_id)
        return {
            "id": order.id,
            "symbol": order.symbol,
            "status": order.status,
            "filled_qty": order.filled_qty,
            "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
        }
    except Exception as exc:
        logger.error("Order status check failed: %s", exc)
        return None
