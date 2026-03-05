"""PrimoGreedy Data API — FastAPI wrapper around DuckDB.

Serves seen tickers, paper portfolio, and agent run logs over HTTP.
Secured with X-API-Key header. Runs behind Tailscale (private network).

Usage:
    uvicorn api:app --host 0.0.0.0 --port 8000
"""

import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import duckdb
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

load_dotenv()

DB_PATH = os.getenv("DUCKDB_PATH", "/home/ubuntu/primogreedy/data.duckdb")
API_KEY = os.getenv("VPS_API_KEY", "5ZhJ_T2gTTQp-LAJKdWMvKJgQSqFU8MSfFDAi04tNr0")
MEMORY_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db() -> duckdb.DuckDBPyConnection:
    """Return a fresh connection (DuckDB is single-writer, but reads are fine)."""
    return duckdb.connect(DB_PATH)


def init_db():
    """Create tables if they don't exist."""
    con = get_db()
    con.execute("""
        CREATE SEQUENCE IF NOT EXISTS portfolio_seq START 1;

        CREATE TABLE IF NOT EXISTS seen_tickers (
            ticker    VARCHAR NOT NULL PRIMARY KEY,
            region    VARCHAR DEFAULT 'USA',
            seen_at   TIMESTAMP DEFAULT current_timestamp
        );

        CREATE TABLE IF NOT EXISTS paper_portfolio (
            id            INTEGER PRIMARY KEY DEFAULT nextval('portfolio_seq'),
            ticker        VARCHAR NOT NULL,
            entry_price   DOUBLE NOT NULL,
            date          DATE NOT NULL,
            verdict       VARCHAR NOT NULL,
            source        VARCHAR DEFAULT 'unknown',
            position_size DOUBLE DEFAULT 0,
            created_at    TIMESTAMP DEFAULT current_timestamp,
            UNIQUE (ticker, date)
        );

        CREATE TABLE IF NOT EXISTS agent_runs (
            id          VARCHAR PRIMARY KEY,
            ticker      VARCHAR NOT NULL,
            timestamp   TIMESTAMP DEFAULT current_timestamp,
            status      VARCHAR NOT NULL,
            model       VARCHAR,
            latency_ms  INTEGER,
            region      VARCHAR DEFAULT 'USA'
        );
    """)
    con.close()


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    init_db()
    yield


app = FastAPI(
    title="PrimoGreedy Data API",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def verify_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SeenTickerIn(BaseModel):
    ticker: str
    region: str = "USA"


class TradeIn(BaseModel):
    ticker: str
    entry_price: float
    date: str  # YYYY-MM-DD
    verdict: str
    source: str = "unknown"
    position_size: float = 0.0


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "db": DB_PATH}


# ---------------------------------------------------------------------------
# Seen Tickers
# ---------------------------------------------------------------------------

@app.get("/seen-tickers")
def get_seen_tickers(x_api_key: str = Header(...)):
    verify_key(x_api_key)
    con = get_db()
    cutoff = time.time() - MEMORY_TTL_SECONDS
    cutoff_ts = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    rows = con.execute(
        "SELECT ticker, epoch(seen_at) as ts FROM seen_tickers WHERE seen_at >= ?",
        [cutoff_ts],
    ).fetchall()
    con.close()

    return {row[0]: row[1] for row in rows}


@app.post("/seen-tickers")
def mark_seen(body: SeenTickerIn, x_api_key: str = Header(...)):
    verify_key(x_api_key)
    con = get_db()
    con.execute(
        """INSERT INTO seen_tickers (ticker, region, seen_at)
           VALUES (?, ?, now())
           ON CONFLICT (ticker) DO UPDATE SET seen_at = now(), region = ?""",
        [body.ticker, body.region, body.region],
    )
    con.close()
    return {"status": "ok", "ticker": body.ticker}


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

@app.get("/portfolio")
def get_portfolio(x_api_key: str = Header(...)):
    verify_key(x_api_key)
    con = get_db()
    rows = con.execute(
        "SELECT ticker, entry_price, date, verdict, source FROM paper_portfolio ORDER BY date DESC"
    ).fetchall()
    con.close()

    return [
        {
            "ticker": r[0],
            "entry_price": r[1],
            "date": str(r[2]),
            "verdict": r[3],
            "source": r[4],
        }
        for r in rows
    ]


@app.post("/portfolio")
def record_trade(body: TradeIn, x_api_key: str = Header(...)):
    verify_key(x_api_key)
    con = get_db()
    try:
        con.execute(
            """INSERT INTO paper_portfolio (ticker, entry_price, date, verdict, source, position_size)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [body.ticker, body.entry_price, body.date, body.verdict, body.source, body.position_size],
        )
    except duckdb.ConstraintException:
        con.close()
        return {"status": "duplicate", "ticker": body.ticker, "date": body.date}
    con.close()
    return {"status": "ok", "ticker": body.ticker}


@app.get("/portfolio/evaluate")
def evaluate_portfolio(x_api_key: str = Header(...)):
    """Fetch live prices and compute P&L for all portfolio entries."""
    verify_key(x_api_key)
    con = get_db()
    rows = con.execute(
        "SELECT ticker, entry_price, date, verdict, source FROM paper_portfolio ORDER BY date"
    ).fetchall()
    con.close()

    if not rows:
        return {"report": "Paper Portfolio is empty.", "trades": []}

    trades = []
    total_roi = 0.0
    winners = 0
    valid = 0

    for r in rows:
        ticker, entry, date, verdict, source = r[0], r[1], str(r[2]), r[3], r[4]
        try:
            info = yf.Ticker(ticker).info
            price = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0
            currency = info.get("currency", "USD")

            # Pence → Pounds
            if ticker.endswith(".L") or currency in ("GBp", "GBX"):
                price = price / 100

            if price > 0 and entry > 0:
                gain = ((price - entry) / entry) * 100
                if gain > 0:
                    winners += 1
                total_roi += gain
                valid += 1
                trades.append({
                    "ticker": ticker, "date": date, "entry": entry,
                    "current": round(price, 2), "gain_pct": round(gain, 2),
                    "verdict": verdict,
                })
            else:
                trades.append({
                    "ticker": ticker, "date": date, "entry": entry,
                    "current": None, "gain_pct": None, "verdict": verdict,
                })
        except Exception:
            trades.append({
                "ticker": ticker, "date": date, "entry": entry,
                "current": None, "gain_pct": None, "verdict": verdict,
            })

    avg_roi = total_roi / valid if valid else 0
    win_rate = (winners / valid * 100) if valid else 0

    return {
        "total_calls": len(rows),
        "win_rate": round(win_rate, 1),
        "avg_return": round(avg_roi, 2),
        "trades": trades,
    }
