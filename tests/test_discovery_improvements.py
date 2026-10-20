"""Tests for the Global Hunter discovery / reliability improvements.

Run with:  .venv-test/bin/python tests/test_discovery_improvements.py

Pure-logic / mocked assertions — no live network calls are made.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# Screener: candidate construction + liquidity/quality gates
# ---------------------------------------------------------------------------
print("\n== Screener: candidate filters ==")

import src.discovery.screener as screener

_base_quote = {
    "symbol": "GEM",
    "regularMarketPrice": 5.0,
    "currency": "USD",
    "marketCap": 100_000_000,
    "averageDailyVolume3Month": 200_000,
    "bookValue": 6.0,
    "epsTrailingTwelveMonths": 0.8,
    "priceToBook": 0.83,
    "shortName": "Gem Co",
    "exchange": "NMS",
}

c = screener._candidate_from_quote(dict(_base_quote), "USA")
check("healthy candidate accepted", c is not None and c["ticker"] == "GEM")
check("candidate carries dollar volume", c and c["dollar_volume"] == 5.0 * 200_000)
check("candidate carries usd market cap", c and c["market_cap_usd"] == 100_000_000)
check("candidate quote_source=dynamic", c and c["quote_source"] == "dynamic")

# Illiquid: low average volume is rejected.
q = dict(_base_quote, averageDailyVolume3Month=1_000)
check("illiquid (low volume) rejected", screener._candidate_from_quote(q, "USA") is None)

# Illiquid: low dollar volume is rejected even with modest share volume.
q = dict(_base_quote, regularMarketPrice=0.2, averageDailyVolume3Month=100_000)
check("illiquid (low $ vol) rejected", screener._candidate_from_quote(q, "USA") is None)

# Sub-penny shell rejected.
q = dict(_base_quote, regularMarketPrice=0.05, averageDailyVolume3Month=5_000_000)
check("sub-0.10 price rejected", screener._candidate_from_quote(q, "USA") is None)

# Over-priced rejected.
q = dict(_base_quote, regularMarketPrice=45.0)
check(">MAX_PRICE rejected", screener._candidate_from_quote(q, "USA") is None)

# Market cap too large rejected.
q = dict(_base_quote, marketCap=9_000_000_000)
check("mega-cap rejected", screener._candidate_from_quote(q, "USA") is None)

# Heavily-shorted name rejected.
q = dict(_base_quote, shortPercentOfFloat=0.45)
check("high short-float rejected", screener._candidate_from_quote(q, "USA") is None)

# --- Limit-up / pump guard: huge intraday move vs 3-month avg volume -------
q = dict(_base_quote, regularMarketChangePercent=180.0, averageDailyVolume3Month=30_000,
         regularMarketVolume=9_000_000)
check("intraday pump rejected", screener._candidate_from_quote(q, "USA") is None)

print("\n== Screener: exchange allow-list ==")
check("US allowed exchanges include NMS", "NMS" in screener._ALLOWED_EXCHANGES["USA"])
check("US excludes OTC pink", "PNK" not in screener._ALLOWED_EXCHANGES["USA"])
check("UK includes LSE", "LSE" in screener._ALLOWED_EXCHANGES["UK"])

# ---------------------------------------------------------------------------
# Screener merge: dynamic + seed dedupe
# ---------------------------------------------------------------------------
print("\n== Screener: dynamic+seed merge dedupe ==")

_orig_dynamic = screener._dynamic_screen
_orig_seed = screener._screen_seed_pool


def _fake_dynamic(region, max_results, per_page=100):
    return [{"ticker": "AAA", "price": 1, "quote_source": "dynamic", "score_breakdown": []}]


def _fake_seed(region, extra_tickers=None, max_results=20):
    return [
        {"ticker": "AAA", "price": 1, "quote_source": "seed", "score_breakdown": []},
        {"ticker": "BBB", "price": 2, "quote_source": "seed", "score_breakdown": []},
    ]


screener._dynamic_screen = _fake_dynamic
screener._screen_seed_pool = _fake_seed
try:
    merged = screener.screen_microcaps("USA", max_results=10)
    tickers = [m["ticker"] for m in merged]
    check("merge dedupes tickers", tickers == ["AAA", "BBB"], tickers)
    check("dynamic wins on collision", merged[0]["quote_source"] == "dynamic", merged[0]["quote_source"])
finally:
    screener._dynamic_screen = _orig_dynamic
    screener._screen_seed_pool = _orig_seed

# ---------------------------------------------------------------------------
# Scoring: insider buying boost
# ---------------------------------------------------------------------------
print("\n== Scoring: insider signal ==")

import src.discovery.scoring as scoring

_orig_signal = scoring._insider_signal

scoring._INSIDER_CACHE.clear()
scoring._insider_signal = lambda t: {"sentiment": "Bullish (Net Insider Buying)", "change": 600_000}
try:
    cand = {"ticker": "X", "price": 5, "eps": 1, "book_value": 6,
            "price_to_book": 0.8, "dollar_volume": 0}
    scored = scoring.score_candidate(cand)
    check("insider buy adds +20", any("+20 insider buying" in b for b in scored["score_breakdown"]),
          scored["score_breakdown"])
    check("insider_signal attached", scored["insider_signal"]["change"] == 600_000)

    base_cand = {"ticker": "Y", "price": 5, "eps": 1, "book_value": 6,
                 "price_to_book": 0.8, "dollar_volume": 0}
    scoring._insider_signal = lambda t: {"sentiment": "Bearish (Net Insider Selling)", "change": -10_000}
    scored_bear = scoring.score_candidate(base_cand)
    check("insider sell penalised", any("insider selling" in b for b in scored_bear["score_breakdown"]),
          scored_bear["score_breakdown"])
finally:
    scoring._insider_signal = _orig_signal
    scoring._INSIDER_CACHE.clear()

# Liquidity bonus
cand = {"ticker": "L", "price": 5, "eps": 1, "book_value": 6, "price_to_book": 0.8,
        "dollar_volume": 2_000_000}
scored = scoring.score_candidate(cand, use_insider=False)
check("high-liquidity bonus applied", any("highly liquid" in b for b in scored["score_breakdown"]),
      scored["score_breakdown"])

# Ticker with no insider data still scores on fundamentals
cand = {"ticker": "N", "price": 5, "eps": 1, "book_value": 6, "price_to_book": 0.8}
scored = scoring.score_candidate(cand)
check("no-insider candidate still scores", scored["score"] >= 60, scored["score"])

# ---------------------------------------------------------------------------
# SEC EDGAR: submissions parsing helpers
# ---------------------------------------------------------------------------
print("\n== SEC EDGAR: filing lookup shape ==")

import src.sec_edgar as sec

# _search_filings must use CIK resolution (ticker filter is ignored by EFTS).
_orig_cik = sec._resolve_cik
_orig_get = sec.requests.get


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _fake_get(url, **kwargs):
    if url.endswith("CIK0000320193.json"):
        return _FakeResp({
            "name": "Apple Inc.",
            "filings": {"recent": {
                "form": ["8-K", "10-K", "10-Q"],
                "filingDate": ["2026-10-01", "2025-11-01", "2026-05-01"],
                "accessionNumber": ["000-aaa", "000-bbb", "000-ccc"],
                "primaryDocument": ["a.htm", "aapl-10k.htm", "aapl-10q.htm"],
            }},
        })
    return _FakeResp({}, 404)


sec._resolve_cik = lambda t: "0000320193"
sec.requests.get = _fake_get
try:
    filing = sec._search_filings("AAPL")
    check("picks newest 10-K preferentially", filing and filing["form_type"] == "10-K", filing)
    check("accession has no dashes", filing and "-" not in filing["accession"], filing)
    check("carries primary_doc", filing and filing["primary_doc"] == "aapl-10k.htm", filing)
    check("cik path-form (no leading zeros)", filing and filing["cik"] == "320193", filing)

    url = sec._fetch_filing_index(filing["cik"], filing["accession"], filing["primary_doc"])
    check("primary_doc url built without extra fetch",
          url == "https://www.sec.gov/Archives/edgar/data/320193/000bbb/aapl-10k.htm", url)

    # Falls back to 10-Q when no 10-K present.
    def _fake_get_q(url, **kwargs):
        return _FakeResp({
            "name": "Small Co",
            "filings": {"recent": {
                "form": ["10-Q"],
                "filingDate": ["2026-05-01"],
                "accessionNumber": ["000-ccc"],
                "primaryDocument": ["q.htm"],
            }},
        })

    sec.requests.get = _fake_get_q
    filing_q = sec._search_filings("SMALL")
    check("falls back to 10-Q", filing_q and filing_q["form_type"] == "10-Q", filing_q)
finally:
    sec._resolve_cik = _orig_cik
    sec.requests.get = _orig_get

# ---------------------------------------------------------------------------
# Form 4 insider feed: EFTS _source parsing
# ---------------------------------------------------------------------------
print("\n== Form 4 feed: EFTS parsing ==")

import src.discovery.insider_feed as feed

_orig_get_i = feed.requests.get


def _fake_get_form4(url, **kwargs):
    return _FakeResp({
        "hits": {"hits": [{"_source": {
            "ciks": ["0001234567"],
            "adsh": "0001234567-26-000001",
            "display_names": ["ACME CORP  (ACME)  (CIK 0001234567)"],
            "file_date": "2026-10-19",
            "form_type": "4",
        }}]},
    })


feed.requests.get = _fake_get_form4
try:
    rows = feed.get_sec_form4_feed(max_items=5)
    check("form4 returns a row", len(rows) == 1, rows)
    check("form4 extracts ticker", rows and rows[0]["ticker"] == "ACME", rows)
    check("form4 builds archive url", rows and "/1234567/000123456726000001" in rows[0]["url"], rows)
finally:
    feed.requests.get = _orig_get_i

# ---------------------------------------------------------------------------
# Regression guard: seed pool still present as fallback
# ---------------------------------------------------------------------------
print("\n== Regression: seed pool retained ==")
check("USA seed pool non-empty", len(screener._SEED_POOLS["USA"]) >= 40)
check("UK seed pool non-empty", len(screener._SEED_POOLS["UK"]) >= 10)

# ---------------------------------------------------------------------------
print("\n========================================")
print(f"RESULT: {PASSED} passed, {FAILED} failed")
print("========================================")
sys.exit(1 if FAILED else 0)
