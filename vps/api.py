"""PrimoGreedy Data API — FastAPI wrapper around DuckDB.

Serves seen tickers, paper portfolio, and agent run logs over HTTP.
Secured with X-API-Key header. Runs behind Tailscale (private network).

Usage:
    uvicorn api:app --host 0.0.0.0 --port 8000
"""

import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import duckdb
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

load_dotenv()

DB_PATH = os.getenv("DUCKDB_PATH", "/home/ubuntu/primogreedy/data.duckdb")
API_KEY = os.getenv("VPS_API_KEY", "5ZhJ_T2gTTQp-LAJKdWMvKJgQSqFU8MSfFDAi04tNr0")
MEMORY_TTL_LONG = 30 * 24 * 60 * 60   # 30 days — BUY / STRONG BUY
MEMORY_TTL_SHORT = 14 * 24 * 60 * 60  # 14 days — AVOID / WATCH / unknown


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
            order_id      VARCHAR,
            fill_price    DOUBLE,
            broker_status VARCHAR DEFAULT 'none',
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
    order_id: Optional[str] = None
    fill_price: Optional[float] = None
    broker_status: str = "none"


class TradeFillUpdate(BaseModel):
    order_id: str
    fill_price: Optional[float] = None
    broker_status: str = "filled"


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
    """Return seen tickers with verdict-aware TTL.

    BUY / STRONG BUY verdicts stay locked for 30 days.
    AVOID / WATCH / unknown verdicts expire after 14 days so the
    agent can re-evaluate tickers whose fundamentals may have changed.
    """
    verify_key(x_api_key)
    con = get_db()

    long_cutoff = datetime.fromtimestamp(
        time.time() - MEMORY_TTL_LONG, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")
    short_cutoff = datetime.fromtimestamp(
        time.time() - MEMORY_TTL_SHORT, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")

    rows = con.execute("""
        WITH latest_verdicts AS (
            SELECT ticker, verdict,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
            FROM paper_portfolio
        )
        SELECT st.ticker, epoch(st.seen_at) AS ts
        FROM seen_tickers st
        LEFT JOIN latest_verdicts lv ON st.ticker = lv.ticker AND lv.rn = 1
        WHERE CASE
            WHEN lv.verdict IN ('BUY', 'STRONG BUY') THEN st.seen_at >= ?
            ELSE st.seen_at >= ?
        END
    """, [long_cutoff, short_cutoff]).fetchall()
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
        """SELECT ticker, entry_price, date, verdict, source 
           FROM paper_portfolio 
           WHERE source NOT IN ('deploy-test', 'test_suite', 'vps_test', 'test', 'e2e_test', 'smoke_test')
             AND ticker NOT LIKE 'TEST%' AND ticker != 'PYTH' AND ticker != 'GHACT'
           ORDER BY date DESC"""
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

    # If this is a re-evaluation, update the existing row for this ticker
    # instead of creating a new dated entry.
    if body.source == "reeval_cron":
        existing = con.execute(
            "SELECT COUNT(*) FROM paper_portfolio WHERE ticker = ?",
            [body.ticker],
        ).fetchone()[0]
        if existing > 0:
            con.execute(
                """UPDATE paper_portfolio
                   SET entry_price = ?, date = ?, verdict = ?, source = ?,
                       position_size = ?, order_id = ?, fill_price = ?,
                       broker_status = ?, created_at = current_timestamp
                   WHERE ticker = ?""",
                [body.entry_price, body.date, body.verdict, body.source,
                 body.position_size, body.order_id, body.fill_price,
                 body.broker_status, body.ticker],
            )
            con.close()
            return {"status": "updated", "ticker": body.ticker}

    # Normal insert (morning cron / new discovery)
    try:
        con.execute(
            """INSERT INTO paper_portfolio
               (ticker, entry_price, date, verdict, source, position_size, order_id, fill_price, broker_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [body.ticker, body.entry_price, body.date, body.verdict,
             body.source, body.position_size, body.order_id,
             body.fill_price, body.broker_status],
        )
    except duckdb.ConstraintException:
        con.close()
        return {"status": "duplicate", "ticker": body.ticker, "date": body.date}
    con.close()
    return {"status": "ok", "ticker": body.ticker}


@app.patch("/portfolio/{ticker}/fill")
def update_trade_fill(ticker: str, body: TradeFillUpdate, x_api_key: str = Header(...)):
    """Update a trade's broker fill information after order execution."""
    verify_key(x_api_key)
    con = get_db()
    con.execute(
        """UPDATE paper_portfolio
           SET order_id = ?, fill_price = ?, broker_status = ?
           WHERE ticker = ? AND order_id = ? OR (ticker = ? AND date = current_date)""",
        [body.order_id, body.fill_price, body.broker_status,
         ticker, body.order_id, ticker],
    )
    con.close()
    return {"status": "ok", "ticker": ticker}


@app.delete("/portfolio/{ticker}")
def delete_portfolio_entry(ticker: str, x_api_key: str = Header(...)):
    """Delete all portfolio entries for a given ticker."""
    verify_key(x_api_key)
    con = get_db()
    before = con.execute("SELECT COUNT(*) FROM paper_portfolio WHERE ticker = ?", [ticker]).fetchone()[0]
    con.execute("DELETE FROM paper_portfolio WHERE ticker = ?", [ticker])
    con.close()
    return {"status": "ok", "ticker": ticker, "deleted": before}


@app.post("/portfolio/deduplicate")
def deduplicate_portfolio(x_api_key: str = Header(...)):
    """Keep only the most recent entry per ticker, removing older duplicates."""
    verify_key(x_api_key)
    con = get_db()
    before = con.execute("SELECT COUNT(*) FROM paper_portfolio").fetchone()[0]
    con.execute("""
        DELETE FROM paper_portfolio
        WHERE rowid NOT IN (
            SELECT max(rowid) FROM paper_portfolio GROUP BY ticker
        )
    """)
    after = con.execute("SELECT COUNT(*) FROM paper_portfolio").fetchone()[0]
    con.close()
    return {"status": "ok", "before": before, "after": after, "removed": before - after}


@app.get("/portfolio/evaluate")
def evaluate_portfolio(x_api_key: str = Header(...)):
    """Fetch live prices and compute P&L for all portfolio entries."""
    verify_key(x_api_key)
    con = get_db()
    rows = con.execute(
        """SELECT ticker, entry_price, date, verdict, source 
           FROM paper_portfolio 
           WHERE source NOT IN ('deploy-test', 'test_suite', 'vps_test', 'test', 'e2e_test', 'smoke_test')
             AND ticker NOT LIKE 'TEST%' AND ticker != 'PYTH' AND ticker != 'GHACT'
           ORDER BY date"""
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


# ---------------------------------------------------------------------------
# Portfolio Summary (lightweight, no yFinance calls)
# ---------------------------------------------------------------------------

@app.get("/portfolio/summary")
def portfolio_summary(x_api_key: str = Header(...)):
    """Aggregated portfolio stats without live price lookups."""
    verify_key(x_api_key)
    con = get_db()

    trades = con.execute(
        """SELECT ticker, entry_price, date, verdict, source,
                  position_size, order_id, fill_price, broker_status
           FROM paper_portfolio 
           WHERE source NOT IN ('deploy-test', 'test_suite', 'vps_test', 'test', 'e2e_test', 'smoke_test')
             AND ticker NOT LIKE 'TEST%' AND ticker != 'PYTH' AND ticker != 'GHACT'
           ORDER BY date DESC"""
    ).fetchall()

    seen_count = con.execute("""
        SELECT COUNT(*) FROM seen_tickers
        WHERE ticker NOT LIKE 'TEST%' AND ticker != 'PYTH' AND ticker != 'GHACT'
    """).fetchone()[0]

    runs = con.execute(
        """SELECT id, ticker, timestamp, status, region
           FROM agent_runs 
           WHERE ticker NOT LIKE 'TEST%' AND ticker != 'PYTH' AND ticker != 'GHACT'
           ORDER BY timestamp DESC LIMIT 20"""
    ).fetchall()

    con.close()

    by_verdict: dict[str, int] = {}
    by_source: dict[str, int] = {}
    recent_trades = []

    for r in trades:
        v = _extract_verdict_label(r[3])
        by_verdict[v] = by_verdict.get(v, 0) + 1
        src = r[4] or "unknown"
        by_source[src] = by_source.get(src, 0) + 1

        trade_obj = {
            "ticker": r[0], "entry_price": r[1], "date": str(r[2]),
            "verdict": v, "source": src,
            "position_size": r[5], "order_id": r[6],
            "fill_price": r[7], "broker_status": r[8] or "none",
        }
        if len(recent_trades) < 15:
            recent_trades.append(trade_obj)

    recent_runs = [
        {"id": r[0], "ticker": r[1], "timestamp": str(r[2]),
         "status": r[3], "region": r[4]}
        for r in runs
    ]

    return {
        "total_trades": len(trades),
        "by_verdict": by_verdict,
        "by_source": by_source,
        "seen_tickers_count": seen_count,
        "recent_trades": recent_trades,
        "recent_runs": recent_runs,
    }


def _extract_verdict_label(verdict_text: str) -> str:
    """Pull the verdict keyword from the full verdict text."""
    upper = (verdict_text or "").upper()
    if "STRONG BUY" in upper:
        return "STRONG BUY"
    if "BUY" in upper:
        return "BUY"
    if "WATCH" in upper:
        return "WATCH"
    if "AVOID" in upper:
        return "AVOID"
    return "OTHER"


# ---------------------------------------------------------------------------
# Dashboard (public — no API key, behind Tailscale)
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Serve the live portfolio dashboard."""
    return _DASHBOARD_HTML


# ---------------------------------------------------------------------------
# Dashboard HTML (inline — no static file dependencies)
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PrimoGreedy Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#0a0e17;--s1:#111827;--s2:#1a2236;--bd:#1e293b;--cy:#22d3ee;--pu:#a78bfa;
--gn:#34d399;--rd:#f87171;--yl:#fbbf24;--tx:#e2e8f0;--td:#94a3b8;--tw:#f8fafc}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--tx);line-height:1.5}
.wrap{max-width:1200px;margin:0 auto;padding:24px}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:32px;flex-wrap:wrap;gap:12px}
header h1{font-size:28px;font-weight:800;letter-spacing:-1px}
header h1 span{background:linear-gradient(135deg,var(--cy),var(--pu));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.badge{font-size:12px;padding:4px 12px;border:1px solid var(--bd);border-radius:99px;color:var(--gn);background:rgba(52,211,153,.08)}
.badge .dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--gn);margin-right:6px;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:32px}
.card{background:var(--s1);border:1px solid var(--bd);border-radius:12px;padding:20px}
.card .label{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--td);margin-bottom:4px}
.card .val{font-size:32px;font-weight:700;color:var(--tw)}
.card .sub{font-size:13px;color:var(--td);margin-top:4px}
.card .val.green{color:var(--gn)}.card .val.red{color:var(--rd)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:32px}
@media(max-width:768px){.grid2{grid-template-columns:1fr}}
.panel{background:var(--s1);border:1px solid var(--bd);border-radius:12px;padding:24px}
.panel h2{font-size:16px;margin-bottom:16px;color:var(--tw)}
canvas{max-height:260px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:10px 12px;border-bottom:1px solid var(--bd);color:var(--td);font-weight:600;
text-transform:uppercase;letter-spacing:.5px;font-size:11px;cursor:pointer;user-select:none}
th:hover{color:var(--cy)}
td{padding:10px 12px;border-bottom:1px solid rgba(30,41,59,.5)}
tr:hover td{background:rgba(34,211,238,.03)}
.ticker{font-family:'JetBrains Mono',monospace;font-weight:600;color:var(--cy)}
.verdict-buy{color:var(--gn);font-weight:600}.verdict-watch{color:var(--yl);font-weight:600}
.verdict-avoid{color:var(--rd);font-weight:600}.verdict-strong{color:#2dd4bf;font-weight:700}
.gain-pos{color:var(--gn)}.gain-neg{color:var(--rd)}
.broker-filled{color:var(--gn);font-size:11px}.broker-pending{color:var(--yl);font-size:11px}
.broker-none{color:var(--td);font-size:11px}
.activity{list-style:none;max-height:320px;overflow-y:auto}
.activity li{padding:8px 0;border-bottom:1px solid rgba(30,41,59,.4);font-size:13px;display:flex;justify-content:space-between}
.activity .ts{color:var(--td);font-size:11px;font-family:'JetBrains Mono',monospace}
.refresh{font-size:11px;color:var(--td);text-align:center;margin-top:24px}
.loading{text-align:center;padding:40px;color:var(--td)}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Primo<span>Greedy</span> Dashboard</h1>
  <div class="badge"><span class="dot"></span>Live — Auto-refresh 5m</div>
</header>

<div class="cards" id="cards"><div class="loading">Loading...</div></div>

<div class="grid2">
  <div class="panel"><h2>Verdict Distribution</h2><canvas id="verdictChart"></canvas></div>
  <div class="panel"><h2>Position Sizing</h2><canvas id="sizingChart"></canvas></div>
</div>

<div class="panel" style="margin-bottom:32px">
  <h2>Portfolio</h2>
  <div style="overflow-x:auto">
    <table id="portfolioTable">
      <thead><tr>
        <th data-col="ticker">Ticker</th><th data-col="date">Date</th>
        <th data-col="entry_price">Entry</th><th data-col="verdict">Verdict</th>
        <th data-col="position_size">Kelly %</th><th data-col="broker_status">Broker</th>
        <th data-col="source">Source</th>
      </tr></thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>
</div>

<div class="grid2">
  <div class="panel"><h2>Recent Agent Runs</h2><ul class="activity" id="runs"></ul></div>
  <div class="panel"><h2>Seen Tickers (Active)</h2><div id="seenInfo" style="margin-bottom:12px"></div><ul class="activity" id="seen"></ul></div>
</div>

<div class="refresh" id="refreshNote"></div>
</div>

<script>
const API_KEY = '""" + API_KEY + """';
const H = {'X-API-Key': API_KEY};
let sortCol = 'date', sortAsc = false;

async function load() {
  try {
    const [sumR, seenR] = await Promise.all([
      fetch('/portfolio/summary', {headers: H}),
      fetch('/seen-tickers', {headers: H})
    ]);
    const sum = await sumR.json();
    const seen = await seenR.json();
    renderCards(sum, seen);
    renderVerdictChart(sum.by_verdict);
    renderSizingChart(sum.recent_trades);
    renderTable(sum.recent_trades);
    renderRuns(sum.recent_runs);
    renderSeen(seen);
    document.getElementById('refreshNote').textContent =
      'Last refresh: ' + new Date().toLocaleTimeString() + ' — next in 5 min';
  } catch(e) {
    document.getElementById('cards').innerHTML =
      '<div class="loading">Error loading data: ' + e.message + '</div>';
  }
}

function renderCards(sum, seen) {
  const buys = (sum.by_verdict['BUY']||0) + (sum.by_verdict['STRONG BUY']||0);
  const avoids = sum.by_verdict['AVOID']||0;
  const watches = sum.by_verdict['WATCH']||0;
  const seenCount = Object.keys(seen).length;
  document.getElementById('cards').innerHTML = `
    <div class="card"><div class="label">Total Trades</div><div class="val">${sum.total_trades}</div>
      <div class="sub">${buys} buys, ${watches} watch, ${avoids} avoid</div></div>
    <div class="card"><div class="label">Buy Rate</div>
      <div class="val green">${sum.total_trades ? Math.round(buys/sum.total_trades*100) : 0}%</div>
      <div class="sub">${buys} actionable of ${sum.total_trades}</div></div>
    <div class="card"><div class="label">Seen Tickers</div><div class="val">${seenCount}</div>
      <div class="sub">Active in ledger</div></div>
    <div class="card"><div class="label">Sources</div><div class="val">${Object.keys(sum.by_source).length}</div>
      <div class="sub">${Object.entries(sum.by_source).map(([k,v])=>k+': '+v).join(', ')}</div></div>`;
}

let vChart, sChart;
function renderVerdictChart(bv) {
  const labels = Object.keys(bv), data = Object.values(bv);
  const colors = labels.map(l => l==='BUY'?'#34d399':l==='STRONG BUY'?'#2dd4bf':l==='WATCH'?'#fbbf24':'#f87171');
  if (vChart) vChart.destroy();
  vChart = new Chart(document.getElementById('verdictChart'), {
    type:'doughnut', data:{labels, datasets:[{data, backgroundColor:colors, borderWidth:0}]},
    options:{plugins:{legend:{labels:{color:'#94a3b8',font:{size:12}}}},cutout:'60%'}
  });
}

function renderSizingChart(trades) {
  const filtered = trades.filter(t => t.position_size > 0);
  const labels = filtered.map(t => t.ticker);
  const data = filtered.map(t => t.position_size);
  const colors = filtered.map(t => {
    const v = (t.verdict||'').toUpperCase();
    return v.includes('STRONG')?'#2dd4bf':v.includes('BUY')?'#34d399':v.includes('WATCH')?'#fbbf24':'#f87171';
  });
  if (sChart) sChart.destroy();
  sChart = new Chart(document.getElementById('sizingChart'), {
    type:'bar', data:{labels, datasets:[{label:'Kelly %', data, backgroundColor:colors, borderRadius:4}]},
    options:{plugins:{legend:{display:false}},scales:{
      x:{ticks:{color:'#94a3b8',font:{size:10}},grid:{display:false}},
      y:{ticks:{color:'#94a3b8',callback:v=>v+'%'},grid:{color:'rgba(30,41,59,.5)'}}
    }}
  });
}

function renderTable(trades) {
  const sorted = [...trades].sort((a,b) => {
    let av = a[sortCol], bv = b[sortCol];
    if (typeof av === 'string') { av = av.toLowerCase(); bv = (bv||'').toLowerCase(); }
    if (av < bv) return sortAsc ? -1 : 1;
    if (av > bv) return sortAsc ? 1 : -1;
    return 0;
  });
  const tbody = document.getElementById('tbody');
  tbody.innerHTML = sorted.map(t => {
    const vc = verdictClass(t.verdict);
    const bc = t.broker_status === 'filled' ? 'broker-filled' :
               t.broker_status === 'none' ? 'broker-none' : 'broker-pending';
    return `<tr>
      <td class="ticker">${t.ticker}</td><td>${t.date}</td>
      <td>$${t.entry_price.toFixed(2)}</td><td class="${vc}">${t.verdict}</td>
      <td>${t.position_size > 0 ? t.position_size.toFixed(1)+'%' : '—'}</td>
      <td class="${bc}">${t.broker_status||'none'}</td>
      <td>${t.source}</td></tr>`;
  }).join('');
}

function verdictClass(v) {
  const u = (v||'').toUpperCase();
  if (u.includes('STRONG')) return 'verdict-strong';
  if (u.includes('BUY')) return 'verdict-buy';
  if (u.includes('WATCH')) return 'verdict-watch';
  return 'verdict-avoid';
}

function renderRuns(runs) {
  const el = document.getElementById('runs');
  if (!runs || !runs.length) { el.innerHTML = '<li>No runs recorded</li>'; return; }
  el.innerHTML = runs.map(r =>
    `<li><span><span class="ticker">${r.ticker}</span> — ${r.status} (${r.region||'?'})</span>
     <span class="ts">${r.timestamp?.slice(0,16)||''}</span></li>`
  ).join('');
}

function renderSeen(seen) {
  const tickers = Object.entries(seen).sort((a,b) => b[1]-a[1]);
  const el = document.getElementById('seen');
  document.getElementById('seenInfo').innerHTML =
    `<span style="color:var(--td);font-size:13px">${tickers.length} tickers in active ledger</span>`;
  el.innerHTML = tickers.slice(0, 30).map(([t, ts]) => {
    const d = new Date(ts * 1000);
    return `<li><span class="ticker">${t}</span><span class="ts">${d.toLocaleDateString()}</span></li>`;
  }).join('');
}

document.querySelectorAll('th[data-col]').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.col;
    if (sortCol === col) sortAsc = !sortAsc;
    else { sortCol = col; sortAsc = true; }
    load();
  });
});

load();
setInterval(load, 5 * 60 * 1000);
</script>
</body>
</html>
"""
