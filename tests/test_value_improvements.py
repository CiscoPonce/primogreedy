"""Verify the Global Hunter value-information improvements.

Run with:  python3 tests/test_value_improvements.py
(or:        .venv-test/bin/python tests/test_value_improvements.py)

No network calls are made — these are pure-logic assertions on the
helper functions added/adjusted across the pipeline.
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
# P0: UK pence -> pounds normalization (the critical 100x bug)
# ---------------------------------------------------------------------------
print("\n== P0: per-share currency normalization ==")

from src.whale_hunter import normalize_per_share_metrics, build_value_snapshot, _fmt_money

# UK ticker: yfinance reports EPS/book in GBp, price normalized to GBP already.
eps, bv = normalize_per_share_metrics("AFC.L", "GBp", eps=5.2, book_value=40.0)
check("UK eps pence->pounds", abs(eps - 0.052) < 1e-9, f"eps={eps}")
check("UK book pence->pounds", abs(bv - 0.40) < 1e-9, f"bv={bv}")

# Graham number sanity: before fix sqrt(22.5*5.2*40)=68.4; after sqrt(22.5*.052*.4)=0.684
graham_fixed = (22.5 * eps * bv) ** 0.5
check("Graham number no longer 100x inflated", abs(graham_fixed - 0.684) < 0.01,
      f"graham={graham_fixed:.3f}")

# US ticker: no conversion.
eps2, bv2 = normalize_per_share_metrics("AAPL", "USD", eps=6.15, book_value=3.7)
check("US eps unchanged", abs(eps2 - 6.15) < 1e-9, f"eps={eps2}")
check("US book unchanged", abs(bv2 - 3.7) < 1e-9, f"bv={bv2}")

# ---------------------------------------------------------------------------
# P0: value snapshot enriched + currency labelled (non-US fundamentals)
# ---------------------------------------------------------------------------
print("\n== P0: enriched value snapshot for ALL regions ==")

sample_info = {
    "sector": "Technology",
    "industry": "Software",
    "marketCap": 150_000_000,
    "currentPrice": 2.10,
    "trailingEps": -0.3,
    "bookValue": 1.2,
    "totalRevenue": 40_000_000,
    "revenueGrowth": 0.35,
    "freeCashflow": -5_000_000,
    "totalCash": 25_000_000,
    "totalDebt": 3_000_000,
    "sharesOutstanding": 80_000_000,
    "currentRatio": 3.5,
    "priceToBook": 1.75,
    "trailingPE": None,
    "priceToSalesTrailing12Months": 3.75,
    "fiftyTwoWeekLow": 0.90,
    "fiftyTwoWeekHigh": 3.60,
    "heldPercentInsiders": 0.2,
    "shortPercentOfFloat": 0.05,
    "returnOnEquity": -0.05,
    "grossMargins": 0.7,
    "enterpriseValue": 128_000_000,
    "currency": "USD",
}

snap = build_value_snapshot(sample_info, "TECH", "USD")
check("snapshot is non-empty", isinstance(snap, str) and len(snap) > 100, f"len={len(snap)}")
check("snapshot labels currency", "FINANCIAL SNAPSHOT (USD):" in snap, snap.splitlines()[0])
check("snapshot has revenue", "Revenue (TTM)" in snap)
check("snapshot has cash/debt", "Total Cash" in snap and "Total Debt" in snap)
check("snapshot has cash/share", "Cash/Share" in snap)
check("snapshot has EV/Rev", "EV/Revenue" in snap)
check("snapshot has 52W range", "52W Range" in snap)
check("snapshot has P/E and P/S", "Trailing P/E" in snap and "P/S" in snap)
check("snapshot uses currency symbol ($)", "$" in snap)

# Non-US snapshot labels GBP with the pound sign
snap_gbp = build_value_snapshot(
    {"marketCap": 50_000_000, "currentPrice": 1.5, "trailingEps": 0.05,
     "bookValue": 0.4, "totalRevenue": 10_000_000, "currency": "GBP"},
    "AFC.L", "GBP")
check("GBP snapshot uses pound symbol", "£" in snap_gbp, [l for l in snap_gbp.splitlines() if "Price" in l])

# ---------------------------------------------------------------------------
# P0: currency-labelled money formatting
# ---------------------------------------------------------------------------
print("\n== P0: currency-labelled money formatting ==")
check("USD dollar", _fmt_money(1234.5, "USD") == "$1,234.50", _fmt_money(1234.5, "USD"))
check("GBP pound", _fmt_money(12.5, "GBP") == "£12.50", _fmt_money(12.5, "GBP"))
check("CAD", _fmt_money(7.0, "CAD") == "C$7.00", _fmt_money(7.0, "CAD"))
check("AUD", _fmt_money(3.2, "AUD") == "A$3.20", _fmt_money(3.2, "AUD"))

# ---------------------------------------------------------------------------
# P1: debate HARD DATA includes the new value fields
# ---------------------------------------------------------------------------
print("\n== P1: debate enriched HARD DATA line ==")

from src.agents.debate import _hard_data_line

dstate = {
    "currency": "USD",
    "price": 2.10,
    "eps": -0.3,
    "book_value": 1.2,
    "ebitda": 1_000_000,
    "market_cap": 150_000_000,
    "revenue": 40_000_000,
    "revenue_growth": 0.35,
    "total_cash": 25_000_000,
    "total_debt": 3_000_000,
    "cash_per_share": 0.3125,
    "current_ratio": 3.5,
    "enterprise_value": 128_000_000,
}
line = _hard_data_line(dstate)
check("debate line has price+currency", "Price=$2.10" in line, line)
check("debate line has market cap", "MCap" in line)
check("debate line has revenue", "Revenue=" in line)
check("debate line has revenue growth", "Rev Growth" in line)
check("debate line has cash/debt", "Cash=" in line and "Debt=" in line)
check("debate line has cash/share", "Cash/Share" in line)
check("debate line has current ratio", "Current Ratio" in line)
check("debate line has EV/Rev", "EV/Rev" in line)

# Base case (old signature) still works without new fields
base_line = _hard_data_line({"price": 5.0, "eps": 1.0, "book_value": 2.0, "ebitda": 3.0})
check("debate line works with minimal fields", "Price=$5.00" in base_line and "MCap" not in base_line, base_line)

# ---------------------------------------------------------------------------
# P1: Finnhub symbol suffix stripping
# ---------------------------------------------------------------------------
print("\n== P1: Finnhub symbol suffix stripping ==")

from src.finance_tools import _finnhub_symbol

check("AFC.L -> AFC", _finnhub_symbol("AFC.L") == "AFC", _finnhub_symbol("AFC.L"))
check("NUMI.TO -> NUMI", _finnhub_symbol("NUMI.TO") == "NUMI", _finnhub_symbol("NUMI.TO"))
check("VUL.AX -> VUL", _finnhub_symbol("VUL.AX") == "VUL", _finnhub_symbol("VUL.AX"))
check("AAPL -> AAPL", _finnhub_symbol("AAPL") == "AAPL", _finnhub_symbol("AAPL"))

# ---------------------------------------------------------------------------
# P1: insider feed no longer rejects non-US up front
# ---------------------------------------------------------------------------
print("\n== P1: insider feed handles non-US (no hard reject) ==")

import src.discovery.insider_feed as insider_feed

# Monkeypatch FINNHUB_API_KEY off -> should return graceful "No Data"/"Error", NOT "N/A (non-US)"
os.environ.pop("FINNHUB_API_KEY", None)
res = insider_feed.get_insider_buys("AFC.L")
check("no hard N/A reject for non-US", "non-US" not in str(res), str(res))
check("returns structured dict", {"sentiment", "mspr", "change", "raw_data"} <= set(res.keys()), list(res.keys()))
check("graceful when no key", res["sentiment"] in ("Unknown", "No Data", "Error"), res["sentiment"])

# ---------------------------------------------------------------------------
# P2: currency -> USD market cap conversion
# ---------------------------------------------------------------------------
print("\n== P2: market cap USD conversion ==")

from src.core.ticker_utils import currency_to_usd

check("USD passthrough", currency_to_usd(50_000_000, "USD", "") == 50_000_000)
check("empty/none returns 0", currency_to_usd(0, "GBP", "") == 0)


# ---------------------------------------------------------------------------
# P0: analyst_node normalizes UK price to pounds in the prompt
# ---------------------------------------------------------------------------
print("\n== P0: analyst prompt uses normalized UK price ==")

from src.core.ticker_utils import normalize_price

uk_price = normalize_price(95.0, "AFC.L", "GBP")
check("UK price pence->pounds", abs(uk_price - 0.95) < 1e-9, f"price={uk_price}")
check("US price unchanged", normalize_price(2.10, "AAPL", "USD") == 2.10)
check("UK 52W low normalized", abs(normalize_price(60.0, "AFC.L", "GBP") - 0.60) < 1e-9)


# ---------------------------------------------------------------------------
# P0: debate judge hardening — retry on empty structured output + JSON fallback
# ---------------------------------------------------------------------------
print("\n== P0: debate judge hardening ==")

import types
import src.agents.debate as debate_mod
from src.models.verdict import InvestmentVerdict

V = InvestmentVerdict(
    quantitative_base="q", lynch_pitch="l", munger_invert="m",
    verdict="WATCH", bottom_line="b",
)

# Sanity: the three roles keep three DISTINCT default models.
distinct = len({debate_mod.PITCHER_MODEL, debate_mod.SKEPTIC_MODEL, debate_mod.JUDGE_MODEL})
check("3 distinct debate models by default", distinct == 3, f"distinct={distinct}")
check("judge defaults to Dots3 Note (structured)", "dots-3-note" in debate_mod.JUDGE_MODEL)

# Structured path retries empty (None) responses.
class _FakeStructured:
    def __init__(self):
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        return None if self.calls < 3 else V

class _FakeLLM:
    def with_structured_output(self, schema):
        return _FakeStructured()

_orig_make_llm = debate_mod._make_llm
_orig_time = debate_mod.time
debate_mod._make_llm = lambda model, max_tokens=2048: _FakeLLM()
debate_mod.time = types.SimpleNamespace(sleep=lambda s: None)

try:
    res = debate_mod._structured_verdict_invoke("prompt")
    check("judge retries empty output and recovers", isinstance(res, InvestmentVerdict) and res.verdict == "WATCH")

    # Plain-JSON fallback when structured NEVER yields a verdict.
    class _FakeStructuredNever:
        def invoke(self, prompt):
            return None

    class _FakeLLMJson:
        def with_structured_output(self, schema):
            return _FakeStructuredNever()

        def invoke(self, prompt):
            return type("R", (), {"content": (
                '{"quantitative_base":"q","lynch_pitch":"l","munger_invert":"m",'
                '"verdict":"AVOID","bottom_line":"x"}'
            )})()

    debate_mod._make_llm = lambda model, max_tokens=2048: _FakeLLMJson()
    state = {
        "ticker": "TEST", "company_name": "Test Co", "strategy": "GRAHAM CLASSIC",
        "bull_case": "b", "bear_case": "r", "price": 1.0, "eps": 0.1,
        "book_value": 1.0, "ebitda": 5.0, "currency": "USD",
    }
    out = debate_mod.judge_node(state)
    check("plain-JSON fallback yields verdict", bool(out.get("final_verdict")))
    check("plain-JSON parses AVOID", "AVOID" in out["final_verdict"])
finally:
    debate_mod._make_llm = _orig_make_llm
    debate_mod.time = _orig_time

# LangGraph must NOT drop _structured_result (it must be declared in DebateState).
check(
    "_structured_result declared in DebateState",
    "_structured_result" in debate_mod.DebateState.__annotations__,
)

# ---------------------------------------------------------------------------
print("\n========================================")
print(f"RESULT: {PASSED} passed, {FAILED} failed")
print("========================================")
sys.exit(1 if FAILED else 0)
