"""Bridge between the paper portfolio (whale_hunter/agent) and the backtesting engine.

Converts paper_portfolio.json into Backtrader-compatible signals so you
can measure how well the Graham/Deep-Value screening strategy performs
against a simple Buy-and-Hold baseline.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

from src.core.logger import get_logger

logger = get_logger(__name__)

PORTFOLIO_FILE = "paper_portfolio.json"


def load_paper_portfolio(path: str = PORTFOLIO_FILE) -> list[dict]:
    """Load the paper portfolio JSON into a list of trade records."""
    p = Path(path)
    if not p.exists():
        logger.warning("Portfolio file not found: %s", path)
        return []
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Could not read portfolio: %s", exc)
        return []


def portfolio_to_signals(
    portfolio: list[dict],
    hold_days: int = 30,
) -> dict[str, pd.DataFrame]:
    """Convert paper portfolio entries into per-symbol signal DataFrames.

    For each BUY / STRONG BUY entry, generates a BUY signal on the entry
    date and a SELL signal ``hold_days`` later.  WATCH entries get a
    smaller position size.

    Returns:
        Mapping of symbol -> DataFrame with columns:
        date, trading_signal, position_size, confidence_level
    """
    by_symbol: dict[str, list[dict]] = {}

    for trade in portfolio:
        ticker = trade.get("ticker", "")
        if not ticker:
            continue
        by_symbol.setdefault(ticker, []).append(trade)

    result: dict[str, pd.DataFrame] = {}

    for symbol, trades in by_symbol.items():
        rows = []
        for t in trades:
            entry_date = datetime.strptime(t["date"], "%Y-%m-%d")
            verdict = t.get("verdict", "BUY").upper()

            if "STRONG BUY" in verdict:
                size = 80
                confidence = 0.9
            elif "BUY" in verdict:
                size = 50
                confidence = 0.7
            elif "WATCH" in verdict:
                size = 20
                confidence = 0.4
            else:
                continue

            rows.append({
                "date": entry_date,
                "trading_signal": "BUY",
                "position_size": size,
                "confidence_level": confidence,
            })

            exit_date = entry_date + timedelta(days=hold_days)
            rows.append({
                "date": exit_date,
                "trading_signal": "SELL",
                "position_size": 100,
                "confidence_level": confidence,
            })

        if rows:
            df = pd.DataFrame(rows)
            df = df.sort_values("date").reset_index(drop=True)
            df = df.drop_duplicates(subset=["date"], keep="last")
            result[symbol] = df

    logger.info(
        "Converted %d portfolio entries into signals for %d symbols",
        len(portfolio), len(result),
    )
    return result


def backtest_portfolio(
    portfolio_path: str = PORTFOLIO_FILE,
    hold_days: int = 30,
    output_dir: str = "output/backtests",
) -> dict[str, dict]:
    """Run a backtest for every symbol in the paper portfolio.

    Uses the existing Backtrader-based engine with PrimoAgentStrategy.

    Returns:
        Mapping of symbol -> {primo: metrics_dict, buyhold: metrics_dict}
    """
    from src.backtesting.engine import run_backtest
    from src.backtesting.strategies import PrimoAgentStrategy, BuyAndHoldStrategy
    from src.backtesting.plotting import plot_single_stock

    portfolio = load_paper_portfolio(portfolio_path)
    if not portfolio:
        logger.warning("No trades to backtest")
        return {}

    signals_map = portfolio_to_signals(portfolio, hold_days=hold_days)
    all_results: dict[str, dict] = {}

    for symbol, signals_df in signals_map.items():
        logger.info("Backtesting %s (%d signals)...", symbol, len(signals_df))

        try:
            start_date = signals_df["date"].min() - timedelta(days=5)
            end_date = signals_df["date"].max() + timedelta(days=5)

            ticker = yf.Ticker(symbol)
            ohlc = ticker.history(start=start_date, end=end_date)
            if ohlc.empty:
                logger.warning("No OHLC data for %s – skipping", symbol)
                continue

            ohlc = ohlc.reset_index()

            primo_results, primo_cerebro = run_backtest(
                ohlc,
                PrimoAgentStrategy,
                f"{symbol} PrimoAgent",
                signals_df=signals_df,
            )
            buyhold_results, buyhold_cerebro = run_backtest(
                ohlc,
                BuyAndHoldStrategy,
                f"{symbol} Buy & Hold",
            )

            all_results[symbol] = {
                "primo": primo_results,
                "buyhold": buyhold_results,
            }

            try:
                plot_single_stock(
                    symbol,
                    primo_cerebro,
                    buyhold_cerebro,
                    output_dir,
                    f"portfolio_backtest_{symbol}.png",
                )
            except Exception as exc:
                logger.warning("Chart generation failed for %s: %s", symbol, exc)

            primo_ret = primo_results["Cumulative Return [%]"]
            bh_ret = buyhold_results["Cumulative Return [%]"]
            diff = primo_ret - bh_ret
            logger.info(
                "%s: PrimoAgent %.2f%% vs Buy&Hold %.2f%% (%+.2f%%)",
                symbol, primo_ret, bh_ret, diff,
            )

        except Exception as exc:
            logger.error("Backtest failed for %s: %s", symbol, exc, exc_info=True)
            continue

    if all_results:
        total = len(all_results)
        wins = sum(
            1 for r in all_results.values()
            if r["primo"]["Cumulative Return [%]"] > r["buyhold"]["Cumulative Return [%]"]
        )
        avg_primo = sum(r["primo"]["Cumulative Return [%]"] for r in all_results.values()) / total
        avg_bh = sum(r["buyhold"]["Cumulative Return [%]"] for r in all_results.values()) / total

        logger.info("=== PORTFOLIO BACKTEST SUMMARY ===")
        logger.info("Symbols tested: %d", total)
        logger.info("PrimoAgent wins: %d/%d (%.1f%%)", wins, total, wins / total * 100)
        logger.info("Avg PrimoAgent: %.2f%% | Avg Buy&Hold: %.2f%% | Alpha: %+.2f%%",
                     avg_primo, avg_bh, avg_primo - avg_bh)

    return all_results
